import tarfile
import zipfile
from pathlib import Path

from personify.parsers.google_takeout import GoogleTakeoutParser


def test_google_takeout_detects_extracted_takeout_folder(tmp_path: Path) -> None:
    takeout = tmp_path / "export" / "Takeout"
    takeout.mkdir(parents=True)
    assert GoogleTakeoutParser.detect(tmp_path / "export")


def test_google_takeout_detects_single_archive(tmp_path: Path) -> None:
    archive = tmp_path / "takeout.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Takeout/Mail/All mail.mbox", "")
    assert GoogleTakeoutParser.detect(archive)


def test_google_takeout_detects_folder_of_archive_parts(tmp_path: Path) -> None:
    part = tmp_path / "part-0001.tgz"
    with tarfile.open(part, "w:gz") as tf:
        payload = tmp_path / "payload.txt"
        payload.write_text("x", encoding="utf-8")
        tf.add(payload, arcname="Takeout/Contacts/contacts.vcf")
    assert GoogleTakeoutParser.detect(tmp_path)


def test_google_takeout_parses_mail_and_reports_unknown(fixtures_dir: Path, staging: Path, tmp_path: Path) -> None:
    takeout_mail = tmp_path / "Takeout" / "Mail"
    takeout_mail.mkdir(parents=True)
    (takeout_mail / "All mail.mbox").write_bytes((fixtures_dir / "gmail" / "sample.mbox").read_bytes())
    (tmp_path / "Takeout" / "YouTube and YouTube Music").mkdir(parents=True)

    parser = GoogleTakeoutParser()
    items = list(parser.iter_items(tmp_path, staging))

    assert len(items) == 2
    assert all(item.metadata.get("product") == "Mail" for item in items)
    statuses = {p["product"]: p["status"] for p in parser.last_report["products"]}
    assert statuses["Mail"] == "ingested"
    assert statuses["YouTube and YouTube Music"] == "handler_not_implemented"


def test_google_takeout_parses_calendar_and_contacts(staging: Path, tmp_path: Path) -> None:
    cal = tmp_path / "Takeout" / "Calendar"
    contacts = tmp_path / "Takeout" / "Contacts"
    cal.mkdir(parents=True)
    contacts.mkdir(parents=True)
    (cal / "events.ics").write_text(
        """BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:event-1\nSUMMARY:Team Sync\nDTSTART:20260110T140000Z\nDTEND:20260110T150000Z\nRRULE:FREQ=WEEKLY\nEND:VEVENT\nEND:VCALENDAR\n""",
        encoding="utf-8",
    )
    (contacts / "contacts.vcf").write_text(
        """BEGIN:VCARD\nFN:Jane Doe\nEMAIL:jane@example.com\nTEL:+15551234567\nEND:VCARD\n""",
        encoding="utf-8",
    )

    parser = GoogleTakeoutParser()
    items = list(parser.iter_items(tmp_path, staging))

    kinds = sorted(i.kind for i in items)
    assert kinds == ["calendar_event", "contact"]
    statuses = {p["product"]: p["status"] for p in parser.last_report["products"]}
    assert statuses["Calendar"] == "ingested"
    assert statuses["Contacts"] == "ingested"


def test_google_takeout_handler_error_isolated(staging: Path, tmp_path: Path) -> None:
    (tmp_path / "Takeout" / "Mail").mkdir(parents=True)

    parser = GoogleTakeoutParser()
    original = parser._iter_mail_items

    def boom(_product_dir: Path):
        raise RuntimeError("mail exploded")
        yield  # pragma: no cover

    parser._iter_mail_items = boom  # type: ignore[assignment]
    items = list(parser.iter_items(tmp_path, staging))
    parser._iter_mail_items = original

    assert items == []
    statuses = {p["product"]: p["status"] for p in parser.last_report["products"]}
    assert statuses["Mail"] == "handler_error"
