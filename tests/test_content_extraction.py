from personify.parsers._content import extract_message_text


def test_extract_message_text_skips_metadata_only_blocks() -> None:
    body = extract_message_text(
        [
            {
                "start_timestamp": "2026-03-19T00:15:12Z",
                "stop_timestamp": "2026-03-19T00:15:13Z",
                "flags": None,
                "type": "metadata",
            },
            {"type": "text", "text": "The useful answer."},
        ]
    )

    assert body == "The useful answer."


def test_extract_message_text_renders_tool_use_readably() -> None:
    body = extract_message_text(
        [{"type": "tool_use", "name": "web_search", "input": {"query": "postgres"}}]
    )

    assert "Tool use: web_search" in body
    assert '"query": "postgres"' in body
