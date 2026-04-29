from pathlib import Path

from personify.parsers.chatgpt import ChatGPTParser


def test_chatgpt_parses_two_messages(fixtures_dir: Path, staging: Path) -> None:
    raw = fixtures_dir / "chatgpt"
    assert ChatGPTParser.detect(raw)
    items = list(ChatGPTParser().iter_items(raw, staging))
    assert len(items) == 2
    titles = {i.title for i in items}
    assert titles == {"Test conversation about postgres"}
    roles = {i.metadata["author_role"] for i in items}
    assert roles == {"user", "assistant"}
    assert all(i.kind == "message" for i in items)
    assert all(i.ts is not None for i in items)
