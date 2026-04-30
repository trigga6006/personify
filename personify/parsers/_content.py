from __future__ import annotations

import json
from typing import Any

_METADATA_ONLY_KEYS = {"start_timestamp", "stop_timestamp", "flags", "type"}


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _is_metadata_only(mapping: dict[str, Any]) -> bool:
    return bool(mapping) and set(mapping).issubset(_METADATA_ONLY_KEYS)


def extract_message_text(content: Any) -> str:
    """Best-effort plaintext extraction from chat-message content.

    Handles both shapes used by major LLM exports:
      - ChatGPT:  {"parts": ["..."]}  or {"parts": [{...}]}
      - Claude:   [{"type": "text", "text": "..."}]
      - plain str / list-of-str / nested dicts with "content"

    Unknown dict shapes fall back to compact JSON, but metadata-only blocks are
    skipped so search results are not dominated by timestamps and flags.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if _is_metadata_only(part):
                    continue
                if "text" in part and part["text"] is not None:
                    out.append(str(part["text"]))
                elif "thinking" in part and part["thinking"] is not None:
                    out.append(str(part["thinking"]))
                elif "content" in part:
                    out.append(extract_message_text(part["content"]))
                elif "parts" in part:
                    out.append(extract_message_text(part["parts"]))
                elif part.get("type") in {"tool_use", "server_tool_use"}:
                    name = part.get("name") or part.get("tool_name") or "tool"
                    rendered = [f"Tool use: {name}"]
                    if part.get("input") is not None:
                        rendered.append(_compact_json(part["input"]))
                    out.append("\n".join(rendered))
                else:
                    out.append(_compact_json(part))
            elif isinstance(part, str):
                out.append(part)
            else:
                out.append(str(part))
        return "\n".join(x for x in out if x.strip())
    if isinstance(content, dict):
        if _is_metadata_only(content):
            return ""
        if isinstance(content.get("parts"), list):
            return extract_message_text(content["parts"])
        if "text" in content and content["text"] is not None:
            return str(content["text"])
        if "thinking" in content and content["thinking"] is not None:
            return str(content["thinking"])
        if "content" in content:
            return extract_message_text(content["content"])
        if content.get("type") in {"tool_use", "server_tool_use"}:
            name = content.get("name") or content.get("tool_name") or "tool"
            rendered = [f"Tool use: {name}"]
            if content.get("input") is not None:
                rendered.append(_compact_json(content["input"]))
            return "\n".join(rendered)
        return _compact_json(content)
    return str(content)
