"""Twitter / X archive parser.

Twitter's "Download an archive of your data" produces a ZIP whose payload
lives under `data/`. Each top-level file is JavaScript: a single
`window.YTD.<name>.part<N> = <json-array>;` assignment. Stripping that prefix
yields a JSON array we can iterate.

This parser handles the entry kinds that carry user-generated content:

  - tweets*.js          → kind = "tweet" | "retweet" | "reply"
  - like*.js            → kind = "like"
  - direct-messages*.js → kind = "dm"

Everything else (followers, blocks, ad data, …) is ignored on purpose so
search and timeline don't get drowned in metadata records. account.js is read
purely to recover the user's screen name as evidence/tag context.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from personify.parsers._zip import find_first, is_supported_archive, unzip_or_passthrough
from personify.parsers.base import ParsedItem, ParserBase

# Matches `window.YTD.<name>.part<N> = ` (and a few legacy variants).
_PREFIX_RE = re.compile(r"^\s*window\.YTD\.[\w_]+\.part\d+\s*=\s*", re.MULTILINE)
# Twitter's "ruby format" timestamps, e.g. "Wed Apr 01 12:00:00 +0000 2024".
_RUBY_TS = "%a %b %d %H:%M:%S %z %Y"


def _parse_ts(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    text = str(raw).strip()
    try:
        d = datetime.strptime(text, _RUBY_TS)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _dedup_preserve_order(values) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _load_archive_js(path: Path) -> list[dict[str, Any]]:
    """Strip the leading `window.YTD…=` and return the parsed JSON array."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    stripped = _PREFIX_RE.sub("", raw, count=1).strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _resolve_data_dir(root: Path) -> Optional[Path]:
    """Return the directory containing tweets.js / account.js, regardless of layout.

    Twitter exports usually unzip to `<root>/data/...`, but some users hand us
    a folder where the archive was extracted into a wrapper directory. Search
    once for the canonical anchor (account.js) and walk up.
    """
    direct = root / "data"
    if (direct / "account.js").exists() or (direct / "tweets.js").exists():
        return direct
    hit = find_first(root, "account.js", "tweets.js")
    if hit:
        return hit.parent
    return None


class TwitterParser(ParserBase):
    SOURCE = "twitter"
    LABEL = "X (Twitter)"
    PARSER_VERSION = "0.1.0"

    @classmethod
    def detect(cls, path: Path) -> bool:
        if path.is_file() and is_supported_archive(path):
            return True
        if path.is_dir():
            data = path / "data"
            if (data / "tweets.js").exists() or (data / "account.js").exists():
                return True
        return False

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        root = unzip_or_passthrough(raw_path, staging_dir)
        data_dir = _resolve_data_dir(root)
        if data_dir is None:
            return

        screen_name = self._screen_name(data_dir)

        for tweets_file in sorted(data_dir.glob("tweets*.js")):
            for entry in _load_archive_js(tweets_file):
                tweet = entry.get("tweet") if isinstance(entry, dict) else None
                if not isinstance(tweet, dict):
                    continue
                yield from self._tweet_item(tweet, screen_name)

        for likes_file in sorted(data_dir.glob("like*.js")):
            for entry in _load_archive_js(likes_file):
                like = entry.get("like") if isinstance(entry, dict) else None
                if not isinstance(like, dict):
                    continue
                yield from self._like_item(like, screen_name)

        for dm_file in sorted(data_dir.glob("direct-messages*.js")):
            for entry in _load_archive_js(dm_file):
                conv = entry.get("dmConversation") if isinstance(entry, dict) else None
                if not isinstance(conv, dict):
                    continue
                yield from self._dm_items(conv, screen_name)

    # ---- per-kind emitters ------------------------------------------------

    def _screen_name(self, data_dir: Path) -> Optional[str]:
        account_path = data_dir / "account.js"
        if not account_path.exists():
            return None
        for entry in _load_archive_js(account_path):
            if not isinstance(entry, dict):
                continue
            account = entry.get("account") or entry
            if isinstance(account, dict) and account.get("username"):
                return str(account["username"])
        return None

    def _tweet_item(self, tweet: dict[str, Any], screen_name: Optional[str]) -> Iterator[ParsedItem]:
        text = tweet.get("full_text") or tweet.get("text") or ""
        if not text.strip():
            return
        ts = _parse_ts(tweet.get("created_at"))
        native_id = tweet.get("id_str") or tweet.get("id")
        is_retweet = bool(tweet.get("retweeted_status")) or text.startswith("RT @")
        is_reply = bool(
            tweet.get("in_reply_to_status_id_str")
            or tweet.get("in_reply_to_status_id")
            or tweet.get("in_reply_to_screen_name")
        )
        kind = "retweet" if is_retweet else "reply" if is_reply else "tweet"

        entities = tweet.get("entities") or {}
        # Tweets often include the same hashtag/mention multiple times. We keep
        # first-seen order so the metadata reads naturally and the downstream
        # graph extractor doesn't try to add duplicate edges.
        hashtags = _dedup_preserve_order(
            str(h.get("text"))
            for h in (entities.get("hashtags") or [])
            if isinstance(h, dict) and h.get("text")
        )
        mentions = _dedup_preserve_order(
            str(u.get("screen_name"))
            for u in (entities.get("user_mentions") or [])
            if isinstance(u, dict) and u.get("screen_name")
        )
        urls = _dedup_preserve_order(
            str(u.get("expanded_url") or u.get("url"))
            for u in (entities.get("urls") or [])
            if isinstance(u, dict) and (u.get("expanded_url") or u.get("url"))
        )

        metadata: dict[str, Any] = {
            "screen_name": screen_name,
            "favorite_count": _to_int(tweet.get("favorite_count")),
            "retweet_count": _to_int(tweet.get("retweet_count")),
            "lang": tweet.get("lang"),
            "in_reply_to_screen_name": tweet.get("in_reply_to_screen_name"),
            "in_reply_to_status_id": (
                tweet.get("in_reply_to_status_id_str") or tweet.get("in_reply_to_status_id")
            ),
            "hashtags": hashtags or None,
            "mentions": mentions or None,
            "urls": urls or None,
        }
        metadata = {k: v for k, v in metadata.items() if v is not None}

        tags: list[tuple[str, str]] = [("kind", kind)]
        if screen_name:
            tags.append(("author", screen_name))
        for tag_text in hashtags:
            tags.append(("hashtag", tag_text))
        for sn in mentions:
            tags.append(("mention", sn))

        title = text.split("\n", 1)[0][:120]

        yield ParsedItem(
            kind=kind,
            title=title,
            body=text,
            ts=ts,
            native_id=str(native_id) if native_id else None,
            metadata=metadata,
            tags=tags,
        )

    def _like_item(self, like: dict[str, Any], screen_name: Optional[str]) -> Iterator[ParsedItem]:
        tweet_id = like.get("tweetId")
        if not tweet_id:
            return
        text = like.get("fullText") or ""
        title = (text or f"liked tweet {tweet_id}").split("\n", 1)[0][:120]
        metadata = {
            "screen_name": screen_name,
            "tweet_id": str(tweet_id),
            "expanded_url": like.get("expandedUrl"),
        }
        metadata = {k: v for k, v in metadata.items() if v is not None}
        tags: list[tuple[str, str]] = [("kind", "like")]
        if screen_name:
            tags.append(("author", screen_name))
        yield ParsedItem(
            kind="like",
            title=title,
            body=text,
            ts=None,  # X exports do not include like timestamps
            native_id=f"like:{tweet_id}",
            metadata=metadata,
            tags=tags,
        )

    def _dm_items(self, conv: dict[str, Any], screen_name: Optional[str]) -> Iterator[ParsedItem]:
        conv_id = conv.get("conversationId")
        for msg in conv.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            create = msg.get("messageCreate")
            if not isinstance(create, dict):
                continue
            text = create.get("text") or ""
            if not text.strip():
                continue
            ts = _parse_ts(create.get("createdAt"))
            native_id = create.get("id")
            metadata = {
                "conversation_id": conv_id,
                "sender_id": create.get("senderId"),
                "recipient_id": create.get("recipientId"),
                "screen_name": screen_name,
            }
            metadata = {k: v for k, v in metadata.items() if v is not None}
            tags: list[tuple[str, str]] = [("kind", "dm")]
            if conv_id:
                tags.append(("conversation", str(conv_id)))
            yield ParsedItem(
                kind="dm",
                title=text.split("\n", 1)[0][:120],
                body=text,
                ts=ts,
                native_id=f"dm:{native_id}" if native_id else None,
                metadata=metadata,
                tags=tags,
            )
