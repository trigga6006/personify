import shutil
import subprocess
from pathlib import Path

import pytest

from personify.parsers.github_repo import GitHubRepoParser


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_github_repo_parses_commits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "initial commit"],
        cwd=repo,
        check=True,
        env={**env, **dict(__import__("os").environ)},
    )

    assert GitHubRepoParser.detect(repo)
    items = list(GitHubRepoParser().iter_items(repo, tmp_path / "staging"))
    kinds = {i.kind for i in items}
    assert "commit" in kinds
    assert "repo_manifest" in kinds
