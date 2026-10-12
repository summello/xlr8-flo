"""E21-S01: delivery status comes from git, not from one checkout's roadmap.yaml.

The pure rules are table-tested. The integration test builds a real repository (main, a milestone
branch, story worktrees) and proves each of the six states, and that the answer does not depend
on which checkout asks.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[4] / "agents" / "scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


D = load("flo_delivery")
R = load("flo_release")


# ── pure rules ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("facts", "state"),
    [
        ({}, "todo"),
        ({"packet": True}, "ready"),
        ({"packet": True, "in_progress": True}, "in_progress"),
        ({"in_progress": True, "committed": True}, "committed"),
        ({"committed": True, "merged": True}, "merged"),
        ({"merged": True, "deployed": True, "packet": True}, "deployed"),
    ],
)
def test_story_state_takes_the_furthest_fact(facts: dict[str, bool], state: str) -> None:
    assert D.story_state(facts) == state


@pytest.mark.parametrize(
    ("states", "ci", "expected"),
    [
        ([], None, "todo"),
        (["todo", "ready"], None, "todo"),
        (["ready", "in_progress"], None, "on_train"),
        (["merged", "ready"], "success", "on_train"),
        (["merged", "merged"], "success", "ready_to_merge"),
        (["merged", "deployed"], "success", "ready_to_merge"),
        (["merged", "merged"], "failure", "on_train"),
        (["merged", "merged"], "pending", "on_train"),
        (["merged", "merged"], None, "on_train"),
        (["deployed", "deployed"], None, "deployed"),
    ],
)
def test_group_state_needs_every_story_merged_and_green_ci(
    states: list[str], ci: str | None, expected: str
) -> None:
    assert D.group_state(states, ci)[0] == expected


def test_an_all_merged_milestone_with_red_ci_says_why() -> None:
    state, note = D.group_state(["merged"], "failure")
    assert state == "on_train" and note == "all stories merged; CI failure"


def test_ci_is_judged_on_the_newest_push_only() -> None:
    runs = [
        {"headSha": "b", "status": "completed", "conclusion": "success"},
        {"headSha": "a", "status": "completed", "conclusion": "failure"},
    ]
    assert D.ci_from_runs(runs, "b") == "success"
    assert D.ci_from_runs(runs, "c") == "pending"  # the branch moved; nothing ran for it yet
    assert D.ci_from_runs([{"headSha": "b", "status": "in_progress"}], "b") == "pending"
    assert D.ci_from_runs([{"headSha": "b", "status": "completed", "conclusion": "failure"}],
                          "b") == "failure"


def test_approvals_need_a_test_running_reviewer_and_the_opus_final_when_required() -> None:
    qwen = {"reviewer": "qwen", "verdict": "approve", "ran_tests": True}
    assert D.approvals_ok([qwen], {})
    assert not D.approvals_ok([{**qwen, "ran_tests": False}], {})
    assert not D.approvals_ok([{**qwen, "verdict": "changes_requested"}], {})
    assert not D.approvals_ok([qwen], {"opus_final": True})
    assert D.approvals_ok([qwen, {**qwen, "reviewer": "opus", "verdict": "approved"}],
                          {"opus_final": True})


# ── a real repository ─────────────────────────────────────────────────────────

def git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def commit(cwd: Path, subject: str, **files: str) -> None:
    for name, text in files.items():
        path = cwd / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "--allow-empty", "-m", subject)


ROADMAP = {
    "milestones": [
        {"id": "M0", "name": "Rails", "branch": "milestone/M0-rails"},
        {"id": "M1", "name": "Spine", "branch": "milestone/M1-spine"},
    ],
    "epics": [
        {"id": "E01", "name": "Platform", "milestone": "M0", "stories": [
            {"id": "E01-S01", "t": "Rails", "k": "chore", "s": "S", "st": "done"}]},
        {"id": "E02", "name": "Budget", "milestone": "M1", "stories": [
            {"id": f"E02-S0{i}", "t": f"Story {i}", "k": "crud", "s": "M", "st": "todo"}
            for i in range(1, 7)]},
    ],
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    commit(root, "init", **{"agents__roadmap.yaml": json.dumps(ROADMAP)})
    commit(root, "E01-S01: Rails")  # on main: deployed
    git(root, "branch", "milestone/M1-spine")
    git(root, "switch", "-q", "milestone/M1-spine")
    commit(root, "E02-S01: merged into the milestone")
    commit(root, "docs: packet", **{"design__E02-S04.md": "# packet\n"})
    git(root, "switch", "-q", "main")
    # E02-S02: commits ahead and an approving review; E02-S03: just started;
    # E02-S06: commits ahead but no review yet.
    for sid, ahead, approved in (("E02-S02", True, True), ("E02-S03", False, False),
                                 ("E02-S06", True, False)):
        wt = tmp_path / f"wt-{sid}"
        git(root, "worktree", "add", "-q", "-b", f"story/{sid}-x", str(wt), "milestone/M1-spine")
        (wt / "task.json").write_text(json.dumps(
            {"story": sid, "milestone_branch": "milestone/M1-spine", "opus_final": False,
             "author": "codex", "reviewers": ["qwen"], "rounds": 1, "notes": {"blocked": None}}))
        if ahead:
            commit(wt, f"wip {sid}", **{f"src__{sid}.py": "x = 1\n"})
        if approved:
            (wt / "reviews").mkdir()
            (wt / "reviews" / f"{sid}.qwen.json").write_text(json.dumps(
                {"reviewer": "qwen", "verdict": "approve", "ran_tests": True}))
    return root


def test_each_state_is_derived_from_git(repo: Path) -> None:
    doc = D.gather(repo, use_gh=False)
    got = {sid: v["state"] for sid, v in doc["stories"].items()}
    assert got == {
        "E01-S01": "deployed",
        "E02-S01": "merged",       # on the milestone branch, not on main
        "E02-S02": "committed",    # commits ahead and an approving review
        "E02-S03": "in_progress",  # a worktree and nothing else
        "E02-S04": "ready",        # a packet and nothing started
        "E02-S05": "todo",
        "E02-S06": "in_progress",  # commits ahead but no review: not committed
    }
    assert doc["milestones"]["M0"]["state"] == "deployed"
    assert doc["milestones"]["M1"]["state"] == "on_train"
    assert doc["counts"]["merged"] == 1 and doc["counts"]["deployed"] == 1


def test_the_answer_does_not_depend_on_which_checkout_asks(repo: Path, tmp_path: Path) -> None:
    from_main = D.gather(repo, use_gh=False)
    from_worktree = D.gather(tmp_path / "wt-E02-S02", use_gh=False)
    for doc in (from_main, from_worktree):
        doc.pop("generated_at"), doc.pop("root")
    assert from_main == from_worktree
    assert D.main_root(tmp_path / "wt-E02-S02") == repo.resolve()


def test_a_milestone_only_leaves_the_train_when_main_has_its_stories(repo: Path) -> None:
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge milestone", "milestone/M1-spine")
    doc = D.gather(repo, use_gh=False)
    assert doc["stories"]["E02-S01"]["state"] == "deployed"
    assert doc["stories"]["E02-S04"]["state"] == "ready"  # still not started
    assert doc["milestones"]["M1"]["state"] == "on_train"


def test_status_is_written_atomically_and_only_where_the_repo_ignores_it(repo: Path) -> None:
    doc = D.gather(repo, use_gh=False)
    out = D.write_status(repo, doc)
    assert out == repo / ".flo" / "status.json"
    assert json.loads(out.read_text())["counts"] == doc["counts"]
    assert not list((repo / ".flo").glob("*.tmp"))
    ignore = (Path(__file__).resolve().parents[4] / ".gitignore").read_text().splitlines()
    assert ".flo/" in ignore


# ── the release runbook ───────────────────────────────────────────────────────

def doc_with(state: str, pr: dict | None) -> dict:
    return {"milestones": {
        "M1": {"name": "Spine", "branch": "milestone/M1-spine", "state": "deployed", "pr": None},
        "M2": {"name": "Demand", "branch": "milestone/M2-demand", "state": state, "pr": pr,
               "note": None},
    }}


def test_runbook_fills_the_pull_request_and_offers_copy_buttons() -> None:
    page = R.runbook_html(doc_with("ready_to_merge", {"number": 16}))
    assert "gh pr merge 16 --merge" in page and "gh pr checks 16" in page
    assert "&lt;PR_NUMBER&gt;" not in page and "reviews/M2.*.json" in page
    assert page.count('class="copy"') >= 10
    assert "FLO_STAGING_DATABASE_URL" in page and "flo.kernel.migrate" in page


def test_runbook_says_when_there_is_no_pull_request_and_when_nothing_is_left() -> None:
    assert "&lt;PR_NUMBER&gt;" in R.runbook_html(doc_with("on_train", None))
    nothing = {"milestones": {"M1": {"name": "x", "branch": "b", "state": "deployed"}}}
    assert "Nothing to release" in R.runbook_html(nothing)


def test_the_board_page_has_a_favicon_that_is_a_valid_data_uri() -> None:
    assert R.FAVICON.startswith('<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,')
    assert "%3Csvg" in R.FAVICON and "rect" in R.FAVICON
