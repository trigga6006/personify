from __future__ import annotations

import csv
import hashlib
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterator

from personify.parsers._zip import extract_tar, extract_zip
from personify.parsers.base import ParsedItem, ParserBase
from personify.parsers.gmail import iter_mbox_messages

TAKEOUT_STATUSES = {
    "ingested",
    "handler_not_implemented",
    "handler_disabled",
    "no_data",
    "handler_error",
}


class GoogleTakeoutParser(ParserBase):
    SOURCE = "google_takeout"
    PARSER_VERSION = "0.3.0"
    LABEL = "Google Takeout"

    @classmethod
    def detect(cls, path: Path) -> bool:
        if path.is_file() and (zipfile.is_zipfile(path) or tarfile.is_tarfile(path)):
            return True
        if path.is_dir() and any(_is_takeout_root_candidate(path).values()):
            return True
        if path.is_dir():
            archives = list(path.glob("*.zip")) + list(path.glob("*.tgz")) + list(path.glob("*.tar.gz")) + list(path.glob("*.tar"))
            return bool(archives)
        return False

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        normalized_root = _normalize_takeout_input(raw_path, staging_dir)
        takeout_root = _resolve_takeout_root(normalized_root)
        product_dirs = _discover_products(takeout_root)

        report: dict[str, object] = {
            "source": self.SOURCE,
            "products_detected": sorted(product_dirs),
            "products": [],
            "warnings": [],
        }

        handlers = {
            "Mail": self._iter_mail_items,
            "Calendar": self._iter_calendar_items,
            "Contacts": self._iter_contact_items,
        }
        for product, product_dir in sorted(product_dirs.items()):
            handler = handlers.get(product)
            if handler is None:
                report["products"].append(_product(product, "handler_not_implemented", 0))
                continue
            try:
                items_seen = 0
                for item in handler(product_dir):
                    items_seen += 1
                    yield item
                status = "ingested" if items_seen else "no_data"
                report["products"].append(_product(product, status, items_seen))
            except Exception as exc:  # noqa: BLE001
                report["products"].append(_product(product, "handler_error", 0, [repr(exc)]))

        self.last_report = report

    def _iter_mail_items(self, product_dir: Path) -> Iterator[ParsedItem]:
        for mbox_path in sorted(product_dir.rglob("*.mbox")):
            for item in iter_mbox_messages(mbox_path):
                item.metadata["product"] = "Mail"
                item.tags.append(("product", "Mail"))
                yield item

    def _iter_calendar_items(self, product_dir: Path) -> Iterator[ParsedItem]:
        for ics in sorted(product_dir.rglob("*.ics")):
            for event in _iter_ics_events(ics):
                event.metadata["product"] = "Calendar"
                event.tags.append(("product", "Calendar"))
                yield event

    def _iter_contact_items(self, product_dir: Path) -> Iterator[ParsedItem]:
        for vcf in sorted(product_dir.rglob("*.vcf")):
            for contact in _iter_vcards(vcf):
                contact.metadata["product"] = "Contacts"
                contact.tags.append(("product", "Contacts"))
                yield contact
        for csv_path in sorted(product_dir.rglob("*.csv")):
            for contact in _iter_contacts_csv(csv_path):
                contact.metadata["product"] = "Contacts"
                contact.tags.append(("product", "Contacts"))
                yield contact


def _product(product: str, status: str, items_seen: int, warnings: list[str] | None = None) -> dict[str, object]:
    return {"product": product, "status": status, "items_seen": items_seen, "warnings": warnings or []}


def _iter_ics_events(path: Path) -> Iterator[ParsedItem]:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = text.split("BEGIN:VEVENT")
    for block in blocks[1:]:
        content = block.split("END:VEVENT", 1)[0]
        fields = _parse_ics_fields(content)
        uid = fields.get("UID")
        summary = fields.get("SUMMARY") or "(no title)"
        start_raw = fields.get("DTSTART")
        end_raw = fields.get("DTEND")
        start = _parse_ics_dt(start_raw) if start_raw else None
        native_id = uid or f"ics-{hashlib.sha256(content.encode()).hexdigest()[:16]}"
        body = f"{summary}\n{fields.get('DESCRIPTION', '')}".strip()
        yield ParsedItem(
            kind="calendar_event",
            title=summary,
            body=body,
            ts=start,
            native_id=native_id,
            metadata={
                "uid": uid,
                "start": start_raw,
                "end": end_raw,
                "location": fields.get("LOCATION"),
                "organizer": fields.get("ORGANIZER"),
                "attendees": [v for k, v in fields.items() if k.startswith("ATTENDEE")],
                "recurrence": fields.get("RRULE"),
                "source_file": path.name,
            },
            tags=[("kind", "calendar_event")],
        )


def _parse_ics_fields(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in block.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key] = value.strip()
    return out


def _parse_ics_dt(raw: str) -> datetime | None:
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _iter_vcards(path: Path) -> Iterator[ParsedItem]:
    text = path.read_text(encoding="utf-8", errors="replace")
    cards = text.split("BEGIN:VCARD")
    for card in cards[1:]:
        content = card.split("END:VCARD", 1)[0]
        fields = _parse_vcard_fields(content)
        emails = [v for k, v in fields.items() if k.startswith("EMAIL")]
        phones = [v for k, v in fields.items() if k.startswith("TEL")]
        name = fields.get("FN") or fields.get("N") or "(no name)"
        native_id = fields.get("UID") or (emails[0] if emails else f"vcf-{hashlib.sha256(content.encode()).hexdigest()[:16]}")
        yield ParsedItem(
            kind="contact",
            title=name,
            body="\n".join([name, *emails, *phones]).strip(),
            native_id=native_id,
            metadata={
                "emails": emails,
                "phones": phones,
                "organization": fields.get("ORG"),
                "birthday": fields.get("BDAY"),
                "urls": [v for k, v in fields.items() if k.startswith("URL")],
                "source_file": path.name,
            },
            tags=[("kind", "contact")],
        )


def _parse_vcard_fields(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in block.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        out[key] = value.strip()
    return out


def _iter_contacts_csv(path: Path) -> Iterator[ParsedItem]:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Name") or row.get("Given Name") or "(no name)"
            email = row.get("E-mail 1 - Value") or row.get("Email")
            phones = [v for k, v in row.items() if "Phone" in k and v]
            native_id = email or f"csv-{hashlib.sha256(str(row).encode()).hexdigest()[:16]}"
            yield ParsedItem(
                kind="contact",
                title=name,
                body="\n".join([name, email or "", *phones]).strip(),
                native_id=native_id,
                metadata={"row": row, "phones": phones, "source_file": path.name},
                tags=[("kind", "contact")],
            )


def _is_takeout_root_candidate(path: Path) -> dict[str, bool]:
    children = {p.name for p in path.iterdir()} if path.is_dir() else set()
    return {
        "takeout": "Takeout" in children,
        "google_takeout": "Google Takeout" in children,
        "has_product_folder": "Mail" in children or "Calendar" in children or "Contacts" in children,
    }


def _normalize_takeout_input(raw_path: Path, staging_dir: Path) -> Path:
    if raw_path.is_dir():
        archives = sorted(
            [*raw_path.glob("*.zip"), *raw_path.glob("*.tgz"), *raw_path.glob("*.tar.gz"), *raw_path.glob("*.tar")]
        )
        if not archives:
            return raw_path
        combined = staging_dir / "takeout_parts"
        combined.mkdir(parents=True, exist_ok=True)
        for idx, archive in enumerate(archives):
            dest = combined / f"part_{idx}"
            dest.mkdir(parents=True, exist_ok=True)
            _extract_archive(archive, dest)
        return combined
    if raw_path.is_file():
        single = staging_dir / "takeout_single"
        single.mkdir(parents=True, exist_ok=True)
        _extract_archive(raw_path, single)
        return single
    return raw_path


def _extract_archive(archive: Path, destination: Path) -> None:
    if zipfile.is_zipfile(archive):
        extract_zip(archive, destination)
        return
    if tarfile.is_tarfile(archive):
        extract_tar(archive, destination)


def _resolve_takeout_root(root: Path) -> Path:
    for candidate in (root / "Takeout", root / "Google Takeout"):
        if candidate.is_dir():
            return candidate
    return root


def _discover_products(takeout_root: Path) -> dict[str, Path]:
    if not takeout_root.is_dir():
        return {}
    return {p.name: p for p in takeout_root.iterdir() if p.is_dir()}
