from __future__ import annotations

import json
from typing import Any


def extract_message_text(content: Any) -> str:
    """Best-effort plaintext extraction from chat-message content.

    Handles both shapes used by major LLM exports:
      - ChatGPT:  {"parts": ["..."]}  or {"parts": [{...}]}
      - Claude:   [{"type": "text", "text": "..."}]
      - plain str / list-of-str / nested dicts with "content"

    Unknown dict shapes fall back to JSON so information is never silently dropped.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if "text" in part:
                    out.append(str(part["text"]))
                elif "content" in part:
                    out.append(extract_message_text(part["content"]))
                elif "parts" in part:
                    out.append(extract_message_text(part["parts"]))
                else:
                    out.append(json.dumps(part))
            elif isinstance(part, str):
                out.append(part)
            else:
                out.append(str(part))
        return "\n".join(out)
    if isinstance(content, dict):
        if isinstance(content.get("parts"), list):
            return extract_message_text(content["parts"])
        if "text" in content:
            return str(content["text"])
        if "content" in content:
            return extract_message_text(content["content"])
        return json.dumps(content)
    return str(content)
