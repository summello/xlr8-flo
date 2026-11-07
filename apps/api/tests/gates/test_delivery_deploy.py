"""E21-S01: "Deployed" needs a green deploy run on main, not just the commit being there."""

from __future__ import annotations

from pathlib import Path

from tests.gates.test_delivery_status import D, commit, git, repo  # noqa: F401  (repo is a fixture)


def run(
    deploy: str, ids: list[str], status: str = "completed", conclusion: str | None = "success"
) -> dict:
    return {
        "sha": "s" + deploy + "".join(ids),
        "status": status,
        "conclusion": conclusion,
        "deploy": deploy,
        "ids": set(ids),
    }


def test_a_story_shipped_if_any_run_containing_it_deployed() -> None:
    newest_failed = [run("failure", ["E02-S01"], conclusion="failure"), run("success", ["E02-S01"])]
    assert D.deploy_verdict("E02-S01", newest_failed) == "success"  # the earlier run shipped it


def test_a_story_whose_only_deploys_failed_or_are_running_is_not_shipped() -> None:
    failed = [run("failure", ["E02-S01"], conclusion="failure")]
    assert D.deploy_verdict("E02-S01", failed) == "failure"
    running = run("pending", ["E02-S01"], status="in_progress", conclusion=None)
    assert D.deploy_verdict("E02-S01", [running]) == "pending"
    assert D.deploy_verdict("E02-S01", [running, *failed]) == "pending"


def test_a_skipped_deploy_ships_nothing_and_fails_nothing() -> None:
    assert D.deploy_verdict("E02-S01", [run("skipped", ["E02-S01"])]) == "success"
    red = run("skipped", ["E02-S01"], conclusion="failure")
    assert D.deploy_verdict("E02-S01", [red]) == "pending"
    assert D.deploy_verdict("E09-S01", [run("success", ["E02-S01"])]) == "success"  # older story


def test_on_main_without_a_green_deploy_is_merged_and_the_milestone_says_why(repo: Path) -> None:  # noqa: F811
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge milestone", "milestone/M1-spine")
    ids = ["E01-S01", "E02-S01"]
    failed = D.gather(repo, use_gh=False, history=[run("failure", ids, conclusion="failure")])
    s = failed["stories"]["E02-S01"]
    assert (s["state"], s["on_main"], s["deploy"]) == ("merged", True, "failure")
    assert failed["stories"]["E01-S01"]["state"] == "merged"
    assert failed["main"]["deploy"] == "failure" and failed["deploy_check"] == "ok"
    green = D.gather(repo, use_gh=False, history=[run("success", ids)])
    assert green["stories"]["E02-S01"]["state"] == "deployed"
    assert green["milestones"]["M0"]["state"] == "deployed"
    unknown = D.gather(repo, use_gh=False)  # runs unreadable: on main still counts, and is flagged
    assert unknown["stories"]["E02-S01"]["state"] == "deployed"
    assert unknown["deploy_check"] == "unavailable"


def test_a_milestone_fully_on_main_but_not_deployed_is_never_ready_to_merge(repo: Path) -> None:  # noqa: F811
    git(repo, "switch", "-q", "milestone/M1-spine")
    for i in range(2, 7):
        commit(repo, f"E02-S0{i}: story")
    git(repo, "switch", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge milestone", "milestone/M1-spine")
    ids = [f"E02-S0{i}" for i in range(1, 7)] + ["E01-S01"]
    running = run("pending", ids, status="in_progress", conclusion=None)
    doc = D.gather(repo, use_gh=False, history=[running])
    assert doc["milestones"]["M1"]["state"] == "on_train"
    assert doc["milestones"]["M1"]["note"] == "on main; deploy running"
