from pathlib import Path

from personify.parsers.discord import DiscordParser


def test_discord_parses(fixtures_dir: Path, staging: Path) -> None:
    raw = fixtures_dir / "discord"
    assert DiscordParser.detect(raw)
    items = list(DiscordParser().iter_items(raw, staging))
    assert len(items) == 2
    assert items[0].metadata["channel_name"] == "general"
    # Second message has an attachment
    assert any(i.media for i in items)
