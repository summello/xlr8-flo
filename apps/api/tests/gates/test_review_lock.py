"""review.sh takes a per-story lock so two reviewers never share one worktree (E23-S14)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[4] / "agents" / "scripts"


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "agents" / "scripts").mkdir(parents=True)
    for name in ("review.sh", "env.sh"):
        shutil.copy(SCRIPTS / name, root / "agents" / "scripts" / name)
    (root / ".scratch").mkdir()
    (tmp_path / "repo-S1" / "reviews").mkdir(parents=True)
    return root


def run(repo):
    return subprocess.run(
        ["sh", str(repo / "agents" / "scripts" / "review.sh"), "S1", "codex"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_live_holder_blocks_a_second_review_and_nothing_is_moved(repo):
    holder = subprocess.Popen(["sleep", "30"])
    try:
        lock = repo / ".scratch" / "S1.review.lock"
        lock.mkdir()
        (lock / "pid").write_text(str(holder.pid))
        verdict = repo.parent / "repo-S1" / "reviews" / "S1.opus.json"
        verdict.write_text("{}")
        done = run(repo)
        assert done.returncode == 75
        assert f"review of S1 already running (pid {holder.pid})" in done.stderr
        assert verdict.exists()
    finally:
        holder.kill()
        holder.wait()


def test_a_dead_holder_is_reclaimed_and_a_killed_reviewers_verdicts_come_back(repo):
    dead = subprocess.Popen(["true"])
    dead.wait()
    lock = repo / ".scratch" / "S1.review.lock"
    lock.mkdir()
    (lock / "pid").write_text(str(dead.pid))
    aside = repo / ".scratch" / "S1.aside.codex"
    aside.mkdir()
    (aside / "S1.opus.json").write_text("{}")
    done = run(repo)  # stops later (no flo checkout here), but only after the lock and the restore
    assert done.returncode != 75
    assert (repo.parent / "repo-S1" / "reviews" / "S1.opus.json").exists()
    assert not aside.exists()
    assert not lock.exists()  # a launch that never started releases its lock
