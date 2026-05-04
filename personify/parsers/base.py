from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional, Protocol


@dataclass
class ParsedItem:
    """A normalized, source-agnostic record yielded by a parser.

    The pipeline is responsible for assigning IDs and persisting; parsers only
    yield ParsedItem instances.
    """

    kind: str
    title: Optional[str] = None
    body: str = ""
    ts: Optional[datetime] = None
    native_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    media: list[dict[str, Any]] = field(default_factory=list)
    tags: list[tuple[str, str]] = field(default_factory=list)


class BaseParser(Protocol):
    SOURCE: str
    PARSER_VERSION: str
    LABEL: Optional[str]

    @classmethod
    def detect(cls, path: Path) -> bool:
        """Return True if this parser can handle the given path."""
        ...

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        """Yield ParsedItem instances. Must not mutate raw_path."""
        ...


class ParserBase:
    """Concrete base class with sensible defaults. Subclass this."""

    SOURCE: str = ""
    PARSER_VERSION: str = "0.0.0"
    # Optional human-friendly display name. Falls back to SOURCE.title() when None.
    LABEL: Optional[str] = None

    @classmethod
    def detect(cls, path: Path) -> bool:  # pragma: no cover - override
        return False

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:  # pragma: no cover
        raise NotImplementedError

    @classmethod
    def display_label(cls) -> str:
        return cls.LABEL or cls.SOURCE.replace("_", " ").title()
