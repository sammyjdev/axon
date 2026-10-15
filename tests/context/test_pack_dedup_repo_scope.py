"""The per-file budget is per (project, file_path): a relative path repeats across repos."""

from __future__ import annotations

from axon.context.pack_dedup import dedup_hits


def _hit(project: str, file_path: str, content: str) -> dict:
    return {"payload": {"project": project, "file_path": file_path, "content": content}}


def test_the_same_relative_path_in_two_repos_does_not_share_a_budget() -> None:
    hits = [_hit("repo_a", "README.md", "about a"), _hit("repo_b", "README.md", "about b")]

    assert dedup_hits(hits, max_per_file=1) == hits


def test_the_budget_still_applies_inside_one_repo() -> None:
    first = _hit("repo_a", "README.md", "one")
    second = _hit("repo_a", "README.md", "two")

    assert dedup_hits([first, second], max_per_file=1) == [first]
