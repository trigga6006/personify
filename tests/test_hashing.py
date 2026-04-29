from pathlib import Path

from personify.util.hashing import sha256_bytes, sha256_file, sha256_text


def test_sha256_text_known() -> None:
    assert (
        sha256_text("hello")
        == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_sha256_bytes_matches_text() -> None:
    assert sha256_bytes(b"hello") == sha256_text("hello")


def test_sha256_file(tmp_path: Path) -> None:
    p = tmp_path / "x.txt"
    p.write_bytes(b"hello")
    digest, size = sha256_file(p)
    assert size == 5
    assert digest == sha256_text("hello")
