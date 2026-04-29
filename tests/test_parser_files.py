from pathlib import Path

from personify.parsers.files import FilesParser


def test_files_parses_md_and_txt(fixtures_dir: Path, staging: Path) -> None:
    raw = fixtures_dir / "files"
    assert FilesParser.detect(raw)
    items = list(FilesParser().iter_items(raw, staging))
    kinds = {i.kind for i in items}
    assert kinds == {"doc"}
    assert {i.title for i in items} == {"note", "todo"}
    assert all(i.native_id for i in items)
