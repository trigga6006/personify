from pathlib import Path
import tarfile
from zipfile import ZipFile

import pytest

from personify.parsers._zip import extract_tar, extract_zip


def test_extract_zip_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with ZipFile(archive, "w") as zf:
        zf.writestr("../outside.txt", "nope")

    with pytest.raises(ValueError, match="Unsafe archive member"):
        extract_zip(archive, tmp_path / "out")


def test_extract_tar_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("nope", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(payload, arcname="../outside.txt")

    with pytest.raises(ValueError, match="Unsafe archive member"):
        extract_tar(archive, tmp_path / "out")
