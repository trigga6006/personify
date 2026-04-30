from pathlib import Path

from personify.parsers.claude_export import ClaudeParser


def test_claude_parses(fixtures_dir: Path, staging: Path) -> None:
    raw = fixtures_dir / "claude"
    assert ClaudeParser.detect(raw)
    items = list(ClaudeParser().iter_items(raw, staging))
    assert len(items) == 2
    senders = {i.metadata["author_role"] for i in items}
    assert senders == {"human", "assistant"}


def test_claude_skips_metadata_only_content_blocks(fixtures_dir: Path, staging: Path) -> None:
    raw = fixtures_dir / "claude"
    items = list(ClaudeParser().iter_items(raw, staging))
    assert items[0].body == "Explain cosine similarity briefly."
