from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from personify.parsers._content import extract_message_text
from personify.parsers._zip import find_first, unzip_or_passthrough
from personify.parsers.base import ParsedItem, ParserBase


def _ts(epoch: Any) -> datetime | None:
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _walk_mapping(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk ChatGPT's conversation mapping in chronological order."""
    nodes = mapping or {}
    # Find root (parent is None) then DFS following children.
    root_id = next((nid for nid, n in nodes.items() if n.get("parent") is None), None)
    if not root_id:
        return list(nodes.values())
    out: list[dict[str, Any]] = []
    stack = [root_id]
    seen: set[str] = set()
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        n = nodes.get(nid) or {}
        out.append(n)
        for child in n.get("children", []) or []:
            stack.append(child)
    return out


class ChatGPTParser(ParserBase):
    SOURCE = "chatgpt"
    PARSER_VERSION = "0.1.0"

    @classmethod
    def detect(cls, path: Path) -> bool:
        if path.is_file() and path.suffix.lower() == ".zip":
            return True
        if path.is_dir() and (path / "conversations.json").exists():
            return True
        return False

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        root = unzip_or_passthrough(raw_path, staging_dir)
        conv_file = find_first(root, "conversations.json")
        if not conv_file:
            return
        conversations = json.loads(conv_file.read_text(encoding="utf-8"))
        for conv in conversations:
            conv_id = conv.get("id") or conv.get("conversation_id")
            title = conv.get("title") or "(untitled)"
            for node in _walk_mapping(conv.get("mapping", {})):
                msg = node.get("message")
                if not msg:
                    continue
                author = (msg.get("author") or {}).get("role") or "unknown"
                text = extract_message_text(msg.get("content"))
                if not text.strip():
                    continue
                ts = _ts(msg.get("create_time"))
                native_id = msg.get("id")
                yield ParsedItem(
                    kind="message",
                    title=title,
                    body=text,
                    ts=ts,
                    native_id=native_id,
                    metadata={
                        "conversation_id": conv_id,
                        "conversation_title": title,
                        "author_role": author,
                        "model": (msg.get("metadata") or {}).get("model_slug"),
                    },
                    tags=[("conversation", str(conv_id) if conv_id else ""), ("role", author)],
                )
