from __future__ import annotations

from pathlib import Path


from personify.parsers.twitter import TwitterParser


def test_detect_directory_layout(fixtures_dir: Path) -> None:
    assert TwitterParser.detect(fixtures_dir / "twitter")


def test_detect_rejects_unrelated_directory(tmp_path: Path) -> None:
    assert TwitterParser.detect(tmp_path) is False


def test_iter_items_emits_tweets_likes_and_dms(fixtures_dir: Path, staging: Path) -> None:
    items = list(TwitterParser().iter_items(fixtures_dir / "twitter", staging))
    kinds = [i.kind for i in items]
    # 3 tweets (1 plain, 1 reply, 1 retweet) + 1 like + 1 dm = 5 entries
    assert kinds.count("tweet") == 1
    assert kinds.count("reply") == 1
    assert kinds.count("retweet") == 1
    assert kinds.count("like") == 1
    assert kinds.count("dm") == 1
    assert len(items) == 5


def test_tweet_metadata_carries_author_and_engagement(fixtures_dir: Path, staging: Path) -> None:
    items = list(TwitterParser().iter_items(fixtures_dir / "twitter", staging))
    plain = next(i for i in items if i.kind == "tweet")
    assert plain.metadata["screen_name"] == "personify_user"
    assert plain.metadata["favorite_count"] == 12
    assert plain.metadata["retweet_count"] == 3
    assert "buildinpublic" in plain.metadata["hashtags"]
    # Hashtag tag emitted for downstream search.
    assert ("hashtag", "buildinpublic") in plain.tags
    # Created-at parsed from Twitter's ruby format.
    assert plain.ts is not None
    assert plain.ts.isoformat().startswith("2024-04-03T09:15:00")


def test_reply_metadata_includes_in_reply_to(fixtures_dir: Path, staging: Path) -> None:
    items = list(TwitterParser().iter_items(fixtures_dir / "twitter", staging))
    reply = next(i for i in items if i.kind == "reply")
    assert reply.metadata["in_reply_to_screen_name"] == "anthropicai"
    assert reply.metadata["in_reply_to_status_id"] == "1699999999999999999"
    mentions = set(reply.metadata.get("mentions") or [])
    assert mentions == {"anthropicai", "codex"}


def test_retweet_kind_classification(fixtures_dir: Path, staging: Path) -> None:
    items = list(TwitterParser().iter_items(fixtures_dir / "twitter", staging))
    rt = next(i for i in items if i.kind == "retweet")
    assert rt.body.startswith("RT @anthropicai")
    assert "anthropicai" in (rt.metadata.get("mentions") or [])


def test_like_native_id_namespaced_to_avoid_collisions_with_tweets(
    fixtures_dir: Path, staging: Path
) -> None:
    items = list(TwitterParser().iter_items(fixtures_dir / "twitter", staging))
    like = next(i for i in items if i.kind == "like")
    assert like.native_id == "like:1500000000000000000"
    assert like.body == "A great thread on knowledge graphs."
    assert like.metadata["expanded_url"].startswith("https://twitter.com")


def test_dm_native_id_namespaced(fixtures_dir: Path, staging: Path) -> None:
    items = list(TwitterParser().iter_items(fixtures_dir / "twitter", staging))
    dm = next(i for i in items if i.kind == "dm")
    assert dm.native_id == "dm:9000000000000000001"
    assert dm.metadata["conversation_id"] == "100100-200200"
    assert dm.metadata["sender_id"] == "100100"
    assert dm.metadata["recipient_id"] == "200200"
    assert dm.ts is not None
    assert dm.ts.isoformat().startswith("2024-04-06T15:30:00")


def test_label_exposed_for_ui(fixtures_dir: Path) -> None:
    """Slug stays "twitter" but display_label drives the UI: "X (Twitter)"."""
    assert TwitterParser.SOURCE == "twitter"
    assert TwitterParser.display_label() == "X (Twitter)"


def test_duplicate_mentions_within_tweet_are_deduplicated(tmp_path: Path, staging: Path) -> None:
    """Real tweets often mention the same handle several times. The parser must
    emit only one ('mention', handle) tag per pair, so the uq_tags_item_kv
    unique constraint isn't violated when persisting.
    """
    arch = tmp_path / "twitter_dup"
    (arch / "data").mkdir(parents=True)
    (arch / "data" / "tweets.js").write_text(
        'window.YTD.tweets.part0 = [\n'
        '  {"tweet": {\n'
        '    "id_str": "1",\n'
        '    "id": "1",\n'
        '    "created_at": "Wed Apr 03 09:15:00 +0000 2024",\n'
        '    "full_text": "@amritwt great point @amritwt agreed",\n'
        '    "entities": {\n'
        '      "hashtags": [{"text": "ai"}, {"text": "ai"}],\n'
        '      "user_mentions": [\n'
        '        {"screen_name": "amritwt"},\n'
        '        {"screen_name": "amritwt"}\n'
        '      ],\n'
        '      "urls": []\n'
        '    }\n'
        '  }}\n'
        ']\n',
        encoding="utf-8",
    )

    items = list(TwitterParser().iter_items(arch, staging))
    assert len(items) == 1
    item = items[0]
    # Tags deduped at the source.
    mention_tags = [t for t in item.tags if t[0] == "mention"]
    hashtag_tags = [t for t in item.tags if t[0] == "hashtag"]
    assert mention_tags == [("mention", "amritwt")]
    assert hashtag_tags == [("hashtag", "ai")]
    # Metadata lists deduped too so downstream graph extraction sees clean data.
    assert item.metadata["mentions"] == ["amritwt"]
    assert item.metadata["hashtags"] == ["ai"]
