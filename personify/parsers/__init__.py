from __future__ import annotations

from typing import Type

from personify.parsers.base import BaseParser, ParsedItem, ParserBase
from personify.parsers.chatgpt import ChatGPTParser
from personify.parsers.claude_export import ClaudeParser
from personify.parsers.discord import DiscordParser
from personify.parsers.files import FilesParser
from personify.parsers.github_repo import GitHubRepoParser
from personify.parsers.gmail import GmailParser
from personify.parsers.notion import NotionParser

PARSERS: dict[str, Type[ParserBase]] = {
    ChatGPTParser.SOURCE: ChatGPTParser,
    ClaudeParser.SOURCE: ClaudeParser,
    GmailParser.SOURCE: GmailParser,
    DiscordParser.SOURCE: DiscordParser,
    NotionParser.SOURCE: NotionParser,
    GitHubRepoParser.SOURCE: GitHubRepoParser,
    FilesParser.SOURCE: FilesParser,
}


def get_parser(source: str) -> Type[ParserBase]:
    try:
        return PARSERS[source]
    except KeyError as e:
        raise ValueError(f"Unknown source '{source}'. Known: {sorted(PARSERS)}") from e


__all__ = ["BaseParser", "ParserBase", "ParsedItem", "PARSERS", "get_parser"]
