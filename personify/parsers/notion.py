from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterator

from personify.parsers._zip import unzip_or_passthrough
from personify.parsers.base import ParsedItem, ParserBase

# Notion page filenames look like: "Page Title abcdef0123456789abcdef0123456789.md"
_NOTION_ID_RE = re.compile(r"\s+([0-9a-f]{32})$", re.IGNORECASE)


def _split_title_and_id(stem: str) -> tuple[str, str | None]:
    m = _NOTION_ID_RE.search(stem)
    if not m:
        return stem, None
    return stem[: m.start()].strip(), m.group(1)


class NotionParser(ParserBase):
    SOURCE = "notion"
    PARSER_VERSION = "0.1.0"

    @classmethod
    def detect(cls, path: Path) -> bool:
        if path.is_file() and path.suffix.lower() == ".zip":
            return True
        if path.is_dir():
            return any(path.rglob("*.md")) or any(path.rglob("*.csv"))
        return False

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        root = unzip_or_passthrough(raw_path, staging_dir)
        for md in sorted(root.rglob("*.md")):
            text = md.read_text(encoding="utf-8", errors="replace")
            title, native_id = _split_title_and_id(md.stem)
            yield ParsedItem(
                kind="page",
                title=title or md.stem,
                body=text,
                native_id=native_id,
                metadata={
                    "relpath": str(md.relative_to(root)),
                    "format": "markdown",
                },
                tags=[("notion", "page")],
            )

        for csv_path in sorted(root.rglob("*.csv")):
            try:
                with csv_path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    for i, row in enumerate(reader):
                        title = next(iter(row.values()), "") or f"row {i}"
                        body = "\n".join(f"{k}: {v}" for k, v in row.items() if v)
                        yield ParsedItem(
                            kind="db_row",
                            title=str(title)[:200],
                            body=body,
                            metadata={
                                "relpath": str(csv_path.relative_to(root)),
                                "row_index": i,
                                "format": "csv",
                                "fields": list(row.keys()),
                            },
                            tags=[("notion", "db_row"), ("table", csv_path.stem)],
                        )
            except (UnicodeDecodeError, csv.Error):
                continue
