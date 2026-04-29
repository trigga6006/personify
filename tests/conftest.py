from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def staging(tmp_path: Path) -> Path:
    p = tmp_path / "staging"
    p.mkdir()
    return p
