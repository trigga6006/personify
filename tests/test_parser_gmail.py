from pathlib import Path

from personify.parsers.gmail import GmailParser


def test_gmail_parses_two_emails(fixtures_dir: Path, staging: Path) -> None:
    mbox = fixtures_dir / "gmail" / "sample.mbox"
    assert GmailParser.detect(mbox)
    items = list(GmailParser().iter_items(mbox, staging))
    assert len(items) == 2
    subjects = {i.title for i in items}
    assert subjects == {"Welcome", "Lunch?"}
    assert all(i.kind == "email" for i in items)
    assert all(i.native_id and i.native_id.startswith("<") for i in items)
