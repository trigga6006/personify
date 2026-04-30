from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from personify.config import settings
from personify.util.hashing import sha256_directory, sha256_file


def ensure_vault_layout() -> None:
    for d in settings.all_dirs():
        d.mkdir(parents=True, exist_ok=True)


def store_raw_export(src: Path, source_slug: str, account_handle: str) -> dict:
    """Copy a raw export into vault/raw/<source>/<account>/<sha-prefix>__<name>.

    Never mutates the source file. Returns a dict with sha256, size, stored_path.
    """
    if not src.exists():
        raise FileNotFoundError(src)

    digest, size = sha256_directory(src) if src.is_dir() else sha256_file(src)
    safe_account = account_handle.replace("/", "_").replace("\\", "_")
    dest_dir = settings.raw_dir / source_slug / safe_account
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{digest[:12]}__{src.name}"
    if not dest.exists():
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
    return {
        "sha256": digest,
        "size_bytes": size,
        "stored_path": str(dest),
        "original_path": str(src.resolve()),
    }


def write_manifest(raw_export_id: int, payload: dict) -> Path:
    settings.manifests_dir.mkdir(parents=True, exist_ok=True)
    p = settings.manifests_dir / f"export_{raw_export_id}.json"
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p


def append_log(name: str, line: str) -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with (settings.logs_dir / f"{name}.log").open("a", encoding="utf-8") as f:
        f.write(f"{ts}\t{line}\n")


def staging_dir_for(raw_export_id: int) -> Path:
    p = settings.staging_dir / f"export_{raw_export_id}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def normalized_path_for(item_id: int) -> Path:
    bucket = f"{item_id // 1000:04d}"
    p = settings.normalized_dir / bucket
    p.mkdir(parents=True, exist_ok=True)
    return p / f"item_{item_id}.json"
