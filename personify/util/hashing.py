from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

_CHUNK = 1 << 20  # 1 MiB


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            h.update(chunk)
    return h.hexdigest(), size


def sha256_directory(path: Path) -> tuple[str, int]:
    """Hash directory contents deterministically by relative path and bytes."""
    h = hashlib.sha256()
    size = 0
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = item.relative_to(path).as_posix().encode("utf-8")
        h.update(rel)
        h.update(b"\0")
        with item.open("rb") as f:
            while True:
                chunk = f.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                h.update(chunk)
        h.update(b"\0")
    return h.hexdigest(), size


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_iter(parts: Iterable[bytes]) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.hexdigest()
