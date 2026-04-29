from __future__ import annotations

import mailbox
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator

from personify.parsers.base import ParsedItem, ParserBase


def _decode_payload(msg) -> str:
    """Best-effort plaintext extraction from an email.message.Message."""
    if msg.is_multipart():
        out = []
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                try:
                    out.append(part.get_content())
                except (LookupError, KeyError):
                    payload = part.get_payload(decode=True) or b""
                    out.append(payload.decode("utf-8", errors="replace"))
        if out:
            return "\n".join(out)
        # Fallback: any text/html
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                return payload.decode("utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return payload or ""


def _ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        d = parsedate_to_datetime(raw)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except (TypeError, ValueError):
        return None


class GmailParser(ParserBase):
    SOURCE = "gmail"
    PARSER_VERSION = "0.1.0"

    @classmethod
    def detect(cls, path: Path) -> bool:
        if path.is_file() and path.suffix.lower() == ".mbox":
            return True
        if path.is_dir():
            return any(path.glob("*.mbox"))
        return False

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        mbox_files: list[Path]
        if raw_path.is_file():
            mbox_files = [raw_path]
        else:
            mbox_files = sorted(raw_path.glob("*.mbox"))

        for mbox_path in mbox_files:
            mbox = mailbox.mbox(str(mbox_path))
            try:
                for key, msg in mbox.iteritems():
                    subject = msg.get("Subject") or "(no subject)"
                    sender = msg.get("From") or ""
                    to = msg.get("To") or ""
                    msg_id = msg.get("Message-ID") or msg.get("Message-Id")
                    body = _decode_payload(msg)
                    ts = _ts(msg.get("Date"))
                    yield ParsedItem(
                        kind="email",
                        title=subject,
                        body=body,
                        ts=ts,
                        native_id=msg_id.strip() if msg_id else None,
                        metadata={
                            "from": sender,
                            "to": to,
                            "cc": msg.get("Cc") or "",
                            "labels": msg.get("X-Gmail-Labels") or "",
                            "thread": msg.get("X-GM-THRID") or "",
                            "mbox": mbox_path.name,
                        },
                        tags=[("from", sender), ("subject", subject)],
                    )
            finally:
                mbox.close()
