from __future__ import annotations

import zipfile
from pathlib import Path


def extract_zip(zip_path: Path, dest: Path) -> Path:
    """Extract zip_path into dest/ (created if missing). Returns dest."""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest)
    return dest


def unzip_or_passthrough(raw_path: Path, staging_dir: Path) -> Path:
    """If raw_path is a .zip, extract into staging_dir and return it; else return raw_path."""
    if raw_path.is_file() and raw_path.suffix.lower() == ".zip":
        return extract_zip(raw_path, staging_dir)
    return raw_path


def find_first(root: Path, *names: str) -> Path | None:
    for name in names:
        for p in root.rglob(name):
            return p
    return None
