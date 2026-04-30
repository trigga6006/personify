from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterator

from personify.parsers._content import extract_message_text
from personify.parsers._zip import find_first, is_supported_archive, unzip_or_passthrough
from personify.parsers.base import ParsedItem, ParserBase


def _ts(s: Any) -> datetime | None:
    if not s:
        return None
    if isinstance(s, (int, float)):
        try:
            return datetime.fromtimestamp(float(s))
        except (OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


class ClaudeParser(ParserBase):
    SOURCE = "claude"
    PARSER_VERSION = "0.1.0"

    @classmethod
    def detect(cls, path: Path) -> bool:
        if path.is_file() and is_supported_archive(path):
            return True
        if path.is_dir() and (path / "conversations.json").exists():
            return True
        return False

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        root = unzip_or_passthrough(raw_path, staging_dir)
        conv_file = find_first(root, "conversations.json")
        if not conv_file:
            return
        data = json.loads(conv_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("conversations", [])

        for conv in data:
            conv_id = conv.get("uuid") or conv.get("id")
            title = conv.get("name") or conv.get("title") or "(untitled)"
            messages = conv.get("chat_messages") or conv.get("messages") or []
            for m in messages:
                text = extract_message_text(m.get("content") or m.get("text"))
                if not text.strip():
                    continue
                role = m.get("sender") or m.get("role") or "unknown"
                ts = _ts(m.get("created_at") or m.get("timestamp"))
                native_id = m.get("uuid") or m.get("id")
                yield ParsedItem(
                    kind="message",
                    title=title,
                    body=text,
                    ts=ts,
                    native_id=native_id,
                    metadata={
                        "conversation_id": conv_id,
                        "conversation_title": title,
                        "author_role": role,
                    },
                    tags=[("conversation", str(conv_id) if conv_id else ""), ("role", role)],
                )
