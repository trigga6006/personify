from pathlib import Path

from personify.parsers.notion import NotionParser


def test_notion_parses_md_and_csv(fixtures_dir: Path, staging: Path) -> None:
    raw = fixtures_dir / "notion"
    assert NotionParser.detect(raw)
    items = list(NotionParser().iter_items(raw, staging))
    pages = [i for i in items if i.kind == "page"]
    rows = [i for i in items if i.kind == "db_row"]
    assert len(pages) == 1
    assert pages[0].title == "Project Notes"
    assert pages[0].native_id == "abcdef0123456789abcdef0123456789"
    assert len(rows) == 2
