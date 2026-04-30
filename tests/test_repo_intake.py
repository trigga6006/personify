from pathlib import Path

from personify.services.repos import discover_git_repos, normalize_repo_key


def test_normalize_repo_key_prefers_owner_repo() -> None:
    assert normalize_repo_key("https://github.com/Acme/Omni-Impact.git", "fallback") == (
        "acme/omni-impact"
    )
    assert normalize_repo_key("git@github.com:Acme/Omni-Impact.git", "fallback") == (
        "acme/omni-impact"
    )


def test_discover_git_repos_from_intake_folder(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    nested = tmp_path / "group" / "repo-c"
    for repo in (repo_a, repo_b, nested):
        (repo / ".git").mkdir(parents=True)

    assert discover_git_repos(tmp_path) == [repo_a.resolve(), repo_b.resolve()]
    assert set(discover_git_repos(tmp_path, recursive=True)) == {
        repo_a.resolve(),
        repo_b.resolve(),
        nested.resolve(),
    }
