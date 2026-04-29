from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Iterator

from personify.parsers._zip import unzip_or_passthrough
from personify.parsers.base import ParsedItem, ParserBase


def _ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class DiscordParser(ParserBase):
    """Parses the Discord 'Request my data' export ZIP.

    Layout (as of 2024):
      messages/
        c<channel_id>/
          channel.json
          messages.csv      # ID, Timestamp, Contents, Attachments
      account/...
      servers/...
    """

    SOURCE = "discord"
    PARSER_VERSION = "0.1.0"

    @classmethod
    def detect(cls, path: Path) -> bool:
        if path.is_file() and path.suffix.lower() == ".zip":
            return True
        if path.is_dir() and (path / "messages").exists():
            return True
        return False

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        root = unzip_or_passthrough(raw_path, staging_dir)
        messages_dir = root / "messages"
        if not messages_dir.exists():
            return

        for channel_dir in sorted(p for p in messages_dir.iterdir() if p.is_dir()):
            chan_meta_file = channel_dir / "channel.json"
            chan_meta: dict = {}
            if chan_meta_file.exists():
                try:
                    chan_meta = json.loads(chan_meta_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    chan_meta = {}
            channel_id = chan_meta.get("id") or channel_dir.name
            channel_name = chan_meta.get("name") or channel_dir.name

            csv_file = channel_dir / "messages.csv"
            if not csv_file.exists():
                continue
            with csv_file.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    body = row.get("Contents") or ""
                    if not body.strip() and not row.get("Attachments"):
                        continue
                    ts = _ts(row.get("Timestamp"))
                    native_id = row.get("ID")
                    media = []
                    if row.get("Attachments"):
                        for url in str(row["Attachments"]).split():
                            media.append({"media_type": "attachment", "path": url})
                    yield ParsedItem(
                        kind="message",
                        title=f"#{channel_name}",
                        body=body,
                        ts=ts,
                        native_id=native_id,
                        metadata={
                            "channel_id": str(channel_id),
                            "channel_name": channel_name,
                            "channel_type": chan_meta.get("type"),
                        },
                        media=media,
                        tags=[("channel", channel_name)],
                    )
