from personify.parsers import PARSERS, get_parser


def test_all_sources_registered() -> None:
    assert set(PARSERS) == {
        "chatgpt",
        "claude",
        "gmail",
        "discord",
        "notion",
        "github",
        "files",
        "twitter",
        "google_takeout",
    }


def test_each_parser_declares_version() -> None:
    for slug, cls in PARSERS.items():
        assert cls.SOURCE == slug
        assert cls.PARSER_VERSION  # non-empty


def test_get_parser_unknown_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        get_parser("nope")
