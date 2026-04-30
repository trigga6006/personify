from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from personify.parsers._zip import is_supported_archive, unzip_or_passthrough
from personify.parsers.base import ParsedItem, ParserBase

_GIT_FORMAT = "%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%b%x1e"
_TEXT_EXTS = {
    ".csv",
    ".md",
    ".rst",
    ".txt",
    ".yaml",
    ".yml",
}
_MAX_TEXT_BYTES = 2_000_000
_MAX_JSON_BYTES = 64_000_000

# Map filename prefix → (kind, title field, body field, timestamp field).
# Covers GitHub's user-data export shape (Settings → Export account data).
_GH_FILE_MAP: dict[str, tuple[str, Optional[str], Optional[str], Optional[str]]] = {
    "pull_requests": ("pr", "title", "body", "created_at"),
    "issues": ("issue", "title", "body", "created_at"),
    "issue_comments": ("issue_comment", None, "body", "created_at"),
    "issue_events": ("issue_event", None, None, "created_at"),
    "commit_comments": ("commit_comment", None, "body", "created_at"),
    "pull_request_review_threads": ("pr_review_thread", None, None, "created_at"),
    "pull_request_review_comments": ("pr_review_comment", None, "body", "created_at"),
    "pull_request_reviews": ("pr_review", None, "body", "submitted_at"),
    "discussions": ("discussion", "title", "body", "created_at"),
    "discussion_comments": ("discussion_comment", None, "body", "created_at"),
    "releases": ("release", "name", "body", "published_at"),
    "milestones": ("milestone", "title", "description", "created_at"),
    "projects": ("project", "name", "body", "created_at"),
    "project_columns": ("project_column", "name", None, "created_at"),
    "project_cards": ("project_card", None, "note", "created_at"),
    "protected_branches": ("protected_branch", "name", None, "created_at"),
    "repositories": ("repository", "name", "description", "created_at"),
    "starred": ("star", None, None, "starred_at"),
    "watched": ("watch", None, None, None),
    "followers": ("follower", None, None, None),
    "following": ("following", None, None, None),
    "users": ("user_record", "name", "bio", "created_at"),
}


def _ts(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


class GitHubRepoParser(ParserBase):
    """Walk a local git repo or GitHub archive export."""

    SOURCE = "github"
    PARSER_VERSION = "0.2.0"

    @classmethod
    def detect(cls, path: Path) -> bool:
        return (path.is_dir() and (path / ".git").exists()) or (
            path.is_file() and is_supported_archive(path)
        )

    def iter_items(self, raw_path: Path, staging_dir: Path) -> Iterator[ParsedItem]:
        root = unzip_or_passthrough(raw_path, staging_dir)
        repo = _find_repo_root(root)
        if repo is None or not repo.is_dir():
            yield from _iter_archive_items(root)
            return
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
        except (OSError, subprocess.CalledProcessError):
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
        except (OSError, subprocess.CalledProcessError):
            tracked = ""

        # 3. If git found nothing at all, fall through to archive walking
        #    so we still capture textual content from things like account
        #    data exports that happen to contain a stray '.git'.
        if not log and not tracked:
            yield from _iter_archive_items(root)
            return
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


def _find_repo_root(root: Path) -> Path | None:
    if root.is_dir() and (root / ".git").exists():
        return root
    if not root.is_dir():
        return None
    for p in root.rglob(".git"):
        if p.is_dir():
            return p.parent
    return None


def _iter_archive_items(root: Path) -> Iterator[ParsedItem]:
    """Walk a GitHub data export.

    GitHub's user-data export bundles JSON files where each file is an
    *array* of records (one file per record type). This walker explodes
    those arrays into one ParsedItem per record so search and timeline
    surface individual PRs / issues / comments instead of giant blobs.
    Plain text files are still indexed as single items.
    """
    if root.is_file():
        files = [root]
        base = root.parent
        archive_name = root.name
    else:
        files = sorted(p for p in root.rglob("*") if p.is_file())
        base = root
        archive_name = root.name

    rels = [str(p.relative_to(base)) for p in files]
    if rels:
        yield ParsedItem(
            kind="archive_manifest",
            title=f"{archive_name} file manifest",
            body="\n".join(rels),
            native_id=f"{archive_name}@manifest",
            metadata={"file_count": len(rels)},
            tags=[("github", "archive")],
        )

    for p in files:
        suffix = p.suffix.lower()
        try:
            size = p.stat().st_size
        except OSError:
            continue
        rel = str(p.relative_to(base))

        if suffix == ".json" and size <= _MAX_JSON_BYTES:
            yield from _iter_json_records(p, rel, size)
            continue

        if suffix in _TEXT_EXTS and size <= _MAX_TEXT_BYTES:
            try:
                body = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            yield ParsedItem(
                kind="archive_file",
                title=p.name,
                body=body,
                native_id=rel,
                metadata={"relpath": rel, "ext": suffix, "size_bytes": size},
                tags=[("github", "archive_file"), ("ext", suffix.lstrip("."))],
            )


def _iter_json_records(p: Path, rel: str, size: int) -> Iterator[ParsedItem]:
    """Parse a GitHub export JSON file and emit one item per record."""
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return

    kind, title_key, body_key, ts_key = _kind_for_filename(p.name)
    records = data if isinstance(data, list) else [data]
    if not records:
        return

    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        item = _record_to_item(rec, rel, idx, kind, title_key, body_key, ts_key)
        if item is not None:
            yield item


def _kind_for_filename(
    name: str,
) -> tuple[str, Optional[str], Optional[str], Optional[str]]:
    base = name.lower()
    # Match the longest prefix first so "issue_comments" wins over "issues".
    for prefix in sorted(_GH_FILE_MAP, key=len, reverse=True):
        if base.startswith(prefix):
            return _GH_FILE_MAP[prefix]
    return ("github_record", None, None, "created_at")


def _record_to_item(
    rec: dict[str, Any],
    rel: str,
    idx: int,
    kind: str,
    title_key: Optional[str],
    body_key: Optional[str],
    ts_key: Optional[str],
) -> Optional[ParsedItem]:
    title = None
    if title_key and rec.get(title_key):
        title = str(rec[title_key])[:300]

    body = ""
    if body_key and rec.get(body_key):
        body = str(rec[body_key])
    if not body:
        # Fall back to a pretty JSON dump so search has something to match.
        body = json.dumps(rec, indent=2, ensure_ascii=False, default=str)

    ts = None
    if ts_key:
        for k in (ts_key, "created_at", "published_at", "submitted_at"):
            if rec.get(k):
                ts = _ts(str(rec[k]))
                if ts:
                    break

    native_id = None
    for k in ("url", "html_url", "id", "node_id", "sha"):
        if rec.get(k) is not None:
            native_id = f"{kind}:{rec[k]}"
            break
    if not native_id:
        native_id = f"{rel}#{idx}"

    repo = _extract_repo(rec)
    user = _extract_user(rec)
    state = rec.get("state")

    if not title:
        title = _derive_title(kind, rec, repo, user)

    tags: list[tuple[str, str]] = [("kind", kind)]
    if repo:
        tags.append(("repo", repo))
    if user:
        tags.append(("author", user))
    if state:
        tags.append(("state", str(state)))

    metadata: dict[str, Any] = {"relpath": rel, "kind": kind}
    for k in (
        "url",
        "html_url",
        "state",
        "merged_at",
        "closed_at",
        "tag_name",
        "labels",
        "assignee",
        "milestone",
        "draft",
        "merged",
    ):
        if k in rec:
            metadata[k] = rec[k]
    if repo:
        metadata["repo"] = repo
    if user:
        metadata["author"] = user

    return ParsedItem(
        kind=kind,
        title=title,
        body=body,
        ts=ts,
        native_id=native_id,
        metadata=metadata,
        tags=tags,
    )


# Match `<owner>/<repo>` from either api.github.com/repos/owner/repo
# or github.com/owner/repo (API URLs use a "/repos/" prefix we must skip).
_REPO_RE = re.compile(r"github\.com/(?:repos/)?([^/]+/[^/?#]+)")
# Match a GitHub username from api.github.com/users/login or github.com/login.
_USER_RE = re.compile(r"github\.com/(?:users/)?([^/?#]+)")


def _extract_repo(rec: dict[str, Any]) -> Optional[str]:
    repo = rec.get("repository") or rec.get("repo")
    if isinstance(repo, str):
        m = _REPO_RE.search(repo)
        if m:
            return m.group(1).rstrip(".git")
        return repo
    if isinstance(repo, dict):
        return repo.get("full_name") or repo.get("name")
    for key in ("url", "html_url", "issue", "commit"):
        v = rec.get(key)
        if isinstance(v, str):
            m = _REPO_RE.search(v)
            if m:
                return m.group(1).rstrip(".git")
    return None


def _extract_user(rec: dict[str, Any]) -> Optional[str]:
    user = rec.get("user") or rec.get("author") or rec.get("actor")
    if isinstance(user, str):
        m = _USER_RE.search(user)
        if m:
            login = m.group(1)
            # Skip the api.github.com path segments themselves.
            if login not in {"repos", "users", "orgs"}:
                return login
        return user
    if isinstance(user, dict):
        return user.get("login") or user.get("name")
    return None


def _derive_title(
    kind: str, rec: dict[str, Any], repo: Optional[str], user: Optional[str]
) -> str:
    snippet = ""
    for k in ("body", "note", "description", "name"):
        v = rec.get(k)
        if isinstance(v, str) and v.strip():
            snippet = v.strip().splitlines()[0][:80]
            break
    parts = [kind]
    if repo:
        parts.append(repo)
    if user:
        parts.append(f"by {user}")
    if snippet:
        parts.append(f"— {snippet}")
    return " · ".join(parts)
