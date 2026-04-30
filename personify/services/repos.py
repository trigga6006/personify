from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from personify.config import settings
from personify.models import RawExport
from personify.services.ingest import ingest_export
from personify.services.register import register_export


@dataclass(frozen=True)
class RepoIdentity:
    key: str
    name: str
    remote_url: str | None = None
    head_sha: str | None = None


@dataclass(frozen=True)
class RepoScanRow:
    path: Path
    identity: RepoIdentity
    duplicate: bool
    existing_export_id: int | None = None


@dataclass(frozen=True)
class RepoRegisterResult:
    path: Path
    identity: RepoIdentity
    status: str
    export_id: int | None = None
    run_id: int | None = None
    error: str | None = None


def discover_git_repos(root: Path, recursive: bool = False) -> list[Path]:
    """Find cloned git repositories under root.

    Non-recursive mode checks root itself and each immediate child. Recursive
    mode walks for `.git` directories and returns their parents.
    """
    root = root.expanduser().resolve()
    repos: set[Path] = set()
    if (root / ".git").exists():
        repos.add(root)
    if recursive:
        for git_dir in root.rglob(".git"):
            if git_dir.is_dir():
                repos.add(git_dir.parent.resolve())
    elif root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and (child / ".git").exists():
                repos.add(child.resolve())
    return sorted(repos)


def git_value(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
            errors="replace",
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return out or None


def normalize_repo_key(remote_url: str | None, fallback_name: str) -> str:
    """Return a stable repo key, preferring owner/name from common Git remotes."""
    if remote_url:
        cleaned = remote_url.strip()
        patterns = [
            r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/#?]+)$",
            r"gitlab\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/#?]+)$",
            r"bitbucket\.org[:/](?P<owner>[^/]+)/(?P<repo>[^/#?]+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, cleaned, flags=re.IGNORECASE)
            if match:
                owner = match.group("owner").lower()
                repo = match.group("repo").removesuffix(".git").lower()
                return f"{owner}/{repo}"
        if cleaned.endswith(".git"):
            cleaned = cleaned[:-4]
        cleaned = cleaned.rstrip("/")
        if cleaned:
            return cleaned.lower()
    return fallback_name.strip().lower()


def repo_identity(repo: Path) -> RepoIdentity:
    remote_url = git_value(repo, "remote", "get-url", "origin")
    head_sha = git_value(repo, "rev-parse", "HEAD")
    key = normalize_repo_key(remote_url, repo.name)
    return RepoIdentity(key=key, name=repo.name, remote_url=remote_url, head_sha=head_sha)


def _manifest_paths() -> Iterable[Path]:
    if not settings.manifests_dir.exists():
        return []
    return settings.manifests_dir.glob("export_*.json")


def existing_repo_exports() -> dict[str, int]:
    """Map repo identity keys already registered in this active vault."""
    existing: dict[str, int] = {}
    for manifest in _manifest_paths():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        repo = data.get("repo")
        export_id = data.get("raw_export_id")
        if isinstance(repo, dict) and repo.get("key") and export_id:
            existing[str(repo["key"])] = int(export_id)
    return existing


def scan_repo_intake(root: Path, recursive: bool = False) -> list[RepoScanRow]:
    existing = existing_repo_exports()
    rows: list[RepoScanRow] = []
    for repo in discover_git_repos(root, recursive=recursive):
        identity = repo_identity(repo)
        existing_export_id = existing.get(identity.key)
        rows.append(
            RepoScanRow(
                path=repo,
                identity=identity,
                duplicate=existing_export_id is not None,
                existing_export_id=existing_export_id,
            )
        )
    return rows


def register_repo_intake(
    root: Path,
    account_handle: str,
    recursive: bool = False,
    ingest: bool = False,
    notes: str | None = None,
) -> list[RepoRegisterResult]:
    results: list[RepoRegisterResult] = []
    for row in scan_repo_intake(root, recursive=recursive):
        if row.duplicate:
            results.append(
                RepoRegisterResult(
                    path=row.path,
                    identity=row.identity,
                    status="duplicate",
                    export_id=row.existing_export_id,
                )
            )
            continue
        try:
            raw = _register_one_repo(row.path, row.identity, account_handle, notes)
            run_id = None
            if ingest and raw.id is not None:
                run = ingest_export(raw.id)
                run_id = run.id
            results.append(
                RepoRegisterResult(
                    path=row.path,
                    identity=row.identity,
                    status="registered",
                    export_id=raw.id,
                    run_id=run_id,
                )
            )
        except Exception as e:  # noqa: BLE001
            results.append(
                RepoRegisterResult(
                    path=row.path,
                    identity=row.identity,
                    status="error",
                    error=repr(e),
                )
            )
    return results


def _register_one_repo(
    repo: Path,
    identity: RepoIdentity,
    account_handle: str,
    notes: str | None,
) -> RawExport:
    return register_export(
        "github",
        repo,
        account_handle,
        notes=notes,
        manifest_extra={
            "repo": _identity_payload(identity),
            "intake_path": str(repo),
        },
    )


def _identity_payload(identity: RepoIdentity) -> dict[str, Any]:
    return {
        "key": identity.key,
        "name": identity.name,
        "remote_url": identity.remote_url,
        "head_sha": identity.head_sha,
    }


def scan_row_payload(row: RepoScanRow) -> dict[str, Any]:
    return {
        "path": str(row.path),
        "repo": _identity_payload(row.identity),
        "duplicate": row.duplicate,
        "existing_export_id": row.existing_export_id,
    }


def register_result_payload(result: RepoRegisterResult) -> dict[str, Any]:
    return {
        "path": str(result.path),
        "repo": _identity_payload(result.identity),
        "status": result.status,
        "export_id": result.export_id,
        "run_id": result.run_id,
        "error": result.error,
    }
