from __future__ import annotations

import gzip
import os
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path


_ARCHIVE_SUFFIXES = {".zip", ".tar", ".tgz", ".gz", ".tar.gz"}


def _force_writable(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _on_rmtree_error(func, path, exc_info):
    # Git pack files (.idx, .pack) are written read-only on Windows; clear the
    # bit and retry so we can wipe a staging dir between ingest runs.
    _force_writable(Path(path))
    try:
        func(path)
    except OSError:
        pass


def _clear_dir(path: Path) -> None:
    """Remove every entry inside `path` (path itself stays).

    Handles Windows-style read-only files (git pack indexes, etc.) by
    flipping the writable bit and retrying.
    """
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, onerror=_on_rmtree_error)
        else:
            try:
                child.unlink()
            except PermissionError:
                _force_writable(child)
                child.unlink()


def is_supported_archive(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES)


def _safe_target(dest: Path, member_name: str) -> Path:
    dest_root = dest.resolve()
    target = (dest / member_name).resolve()
    try:
        target.relative_to(dest_root)
    except ValueError as e:
        raise ValueError(f"Unsafe archive member path: {member_name}") from e
    return target


def extract_zip(zip_path: Path, dest: Path) -> Path:
    """Extract zip_path into dest/ (created if missing). Returns dest."""
    dest.mkdir(parents=True, exist_ok=True)
    _clear_dir(dest)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            _safe_target(dest, info.filename)
        zf.extractall(dest)
    return dest


def extract_tar(tar_path: Path, dest: Path) -> Path:
    """Extract tar_path into dest/ safely. Returns dest."""
    dest.mkdir(parents=True, exist_ok=True)
    _clear_dir(dest)
    with tarfile.open(tar_path, "r:*") as tf:
        for info in tf.getmembers():
            _safe_target(dest, info.name)
            if info.issym() or info.islnk():
                raise ValueError(f"Unsafe archive link member: {info.name}")
        tf.extractall(dest)
    return dest


def extract_gzip(gzip_path: Path, dest: Path) -> Path:
    """Decompress a single .gz file into dest/. Returns the decompressed path."""
    dest.mkdir(parents=True, exist_ok=True)
    out_name = gzip_path.name[:-3] if gzip_path.name.lower().endswith(".gz") else gzip_path.stem
    out_path = _safe_target(dest, out_name)
    with gzip.open(gzip_path, "rb") as src, out_path.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    return out_path


def unzip_or_passthrough(raw_path: Path, staging_dir: Path) -> Path:
    """Extract supported archives into staging_dir; otherwise return raw_path."""
    if not raw_path.is_file():
        return raw_path
    if raw_path.suffix.lower() == ".zip":
        return extract_zip(raw_path, staging_dir)
    if tarfile.is_tarfile(raw_path):
        return extract_tar(raw_path, staging_dir)
    if raw_path.name.lower().endswith(".gz"):
        return extract_gzip(raw_path, staging_dir)
    return raw_path


def find_first(root: Path, *names: str) -> Path | None:
    for name in names:
        for p in root.rglob(name):
            return p
    return None
