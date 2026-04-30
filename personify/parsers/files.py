from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from personify.parsers.base import ParsedItem, ParserBase
from personify.parsers._zip import is_supported_archive, unzip_or_passthrough

_TEXT_EXTS = {".json", ".jsonl", ".md", ".markdown", ".txt", ".rst", ".log", ".csv"}


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


class FilesParser(ParserBase):
    """Generic local folder of markdown / text / PDF files."""

    SOURCE = "files"
    PARSER_VERSION = "0.1.0"

    @classmethod
    def detect(cls, path: Path) -> bool:
        if not path.is_dir():
            return path.is_file() and (
                path.suffix.lower() in _TEXT_EXTS
                or path.suffix.lower() == ".pdf"
                or is_supported_archive(path)
            )
        for p in path.rglob("*"):
            if p.is_file() and (p.suffix.lower() in _TEXT_EXTS or p.suffix.lower() == ".pdf"):
                return True
        return False

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        raw_path = unzip_or_passthrough(raw_path, staging_dir)
        targets: list[Path]
        if raw_path.is_file():
            targets = [raw_path]
        else:
            targets = [
                p
                for p in raw_path.rglob("*")
                if p.is_file() and (p.suffix.lower() in _TEXT_EXTS or p.suffix.lower() == ".pdf")
            ]

        for p in sorted(targets):
            try:
                if p.suffix.lower() == ".pdf":
                    body = _read_pdf(p)
                    fmt = "pdf"
                else:
                    body = p.read_text(encoding="utf-8", errors="replace")
                    fmt = "text"
            except OSError:
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            except OSError:
                mtime = None
            rel = p.relative_to(raw_path) if raw_path.is_dir() else p.name
            yield ParsedItem(
                kind="doc",
                title=p.stem,
                body=body,
                ts=mtime,
                native_id=str(rel),
                metadata={
                    "relpath": str(rel),
                    "format": fmt,
                    "ext": p.suffix.lower(),
                },
                tags=[("ext", p.suffix.lower().lstrip("."))],
            )
