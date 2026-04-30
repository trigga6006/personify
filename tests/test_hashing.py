from pathlib import Path

from personify.util.hashing import sha256_bytes, sha256_directory, sha256_file, sha256_text


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


def test_sha256_directory_is_order_independent(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    (a / "nested").mkdir(parents=True)
    (b / "nested").mkdir(parents=True)
    (a / "one.txt").write_text("1", encoding="utf-8")
    (a / "nested" / "two.txt").write_text("2", encoding="utf-8")
    (b / "nested" / "two.txt").write_text("2", encoding="utf-8")
    (b / "one.txt").write_text("1", encoding="utf-8")

    assert sha256_directory(a) == sha256_directory(b)
