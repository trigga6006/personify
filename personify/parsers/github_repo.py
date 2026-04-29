from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Iterator

from personify.parsers.base import ParsedItem, ParserBase

_GIT_FORMAT = "%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%b%x1e"


def _ts(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


class GitHubRepoParser(ParserBase):
    """Walk a local git repo: index commits + a manifest of tracked files."""

    SOURCE = "github"
    PARSER_VERSION = "0.1.0"

    @classmethod
    def detect(cls, path: Path) -> bool:
        return path.is_dir() and (path / ".git").exists()

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        repo = raw_path
        repo_name = repo.name

        # 1. Commits via git log.
        try:
            log = subprocess.run(
                ["git", "log", f"--format={_GIT_FORMAT}", "--all"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
                encoding="utf-8",
                errors="replace",
            ).stdout
        except (FileNotFoundError, subprocess.CalledProcessError):
            log = ""

        for record in log.split("\x1e"):
            record = record.strip()
            if not record:
                continue
            parts = record.split("\x1f")
            if len(parts) < 5:
                continue
            sha, an, ae, aiso, subject, *rest = parts
            body = rest[0] if rest else ""
            yield ParsedItem(
                kind="commit",
                title=subject,
                body=(subject + ("\n\n" + body if body else "")),
                ts=_ts(aiso),
                native_id=f"{repo_name}@{sha}",
                metadata={
                    "repo": repo_name,
                    "sha": sha,
                    "author_name": an,
                    "author_email": ae,
                },
                tags=[("repo", repo_name), ("author_email", ae)],
            )

        # 2. Tracked file manifest as a single doc item per repo.
        try:
            tracked = subprocess.run(
                ["git", "ls-files"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
                encoding="utf-8",
                errors="replace",
            ).stdout
        except (FileNotFoundError, subprocess.CalledProcessError):
            tracked = ""
        files = [f for f in tracked.splitlines() if f]
        if files:
            yield ParsedItem(
                kind="repo_manifest",
                title=f"{repo_name} file manifest",
                body="\n".join(files),
                native_id=f"{repo_name}@manifest",
                metadata={"repo": repo_name, "file_count": len(files)},
                tags=[("repo", repo_name)],
            )
