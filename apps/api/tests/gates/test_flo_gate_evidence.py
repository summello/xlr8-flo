"""`flo gate` believes `ran_tests` only when flo itself recorded a passing check (E23-S08).

The boolean used to be the reviewer's word. Now a verdict must name the submitted commit, and the
gate looks for a record flo wrote for that commit: passing, on a clean tree, for the right profile.
A gate that cannot be seen to fail is not a gate, so every rule here has a planted counter-case.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from tests.gates.flo_loader import load_flo
from tests.gates.test_flo_assign import fleet

F = load_flo()
SID = "E00-S01"
SHA = "a" * 40
OPUS_MESSAGE = "Opus final review required"


def record(**over):
    base = {
        "profile": "light",
        "commit": SHA,
        "tree": "t",
        "clean": True,
        "result": "pass",
        "steps": [{"name": "ruff", "ok": True}],
        "seconds": 3,
        "at": "2026-10-22T10:00:00+0000",
    }
    base.update(over)
    return base


def verdict(**over):
    base = {
        "story": SID,
        "reviewer": "codex",
        "verdict": "approve",
        "ran_tests": True,
        "evidence": {"commit": SHA, "profile": "light"},
        "findings": [],
    }
    base.update(over)
    return base


def packet(**over):
    base = {
        "author": "sonnet",
        "reviewers": ["codex"],
        "opus_final": False,
        "tags": [],
        "substitutions": [],
        "traceability": {"SUP-005": "test_x"},
        "notes": {"blocked": None, "recap": "Adds a check that operators can rely on today."},
        "submitted_sha": SHA,
        "evidence_required": True,
        "evidence": [record()],
    }
    base.update(over)
    return base


@pytest.fixture
def gate(tmp_path, monkeypatch, capsys):
    wt = tmp_path / "wt"
    (wt / "reviews").mkdir(parents=True)
    config = fleet()
    config["policy"]["min_reviewers"] = 1
    monkeypatch.setattr(F, "worktree_path", lambda sid: wt)
    monkeypatch.setattr(F, "load_yaml", lambda path: config)
    monkeypatch.setattr(F, "checked", lambda wt_, profile: True)
    monkeypatch.setattr(F, "story_ci_failure", lambda pkt, wt_: None)

    def run(pkt=None, reviews=None):
        (wt / "task.json").write_text(json.dumps(pkt if pkt is not None else packet()))
        for old in (wt / "reviews").glob("*.json"):
            old.unlink()
        for review in [verdict()] if reviews is None else reviews:
            (wt / "reviews" / f"{SID}.{review['reviewer']}.json").write_text(json.dumps(review))
        try:
            F.cmd_gate([SID])
        except SystemExit as exc:
            return exc.code, capsys.readouterr().out
        return 0, capsys.readouterr().out

    return run


def test_a_claim_with_a_matching_record_passes(gate):
    code, out = gate()
    assert code == 0, out
    assert "GATE PASSED" in out


def test_ran_tests_true_with_no_record_is_refused(gate):
    code, out = gate(packet(evidence=[]))
    assert code == 1
    assert "codex: ran_tests=true but flo holds no passing clean light check" in out
    assert "0/1 reviews" in out


def test_a_verdict_without_an_evidence_block_is_refused(gate):
    review = verdict()
    del review["evidence"]
    code, out = gate(reviews=[review])
    assert code == 1
    assert "carries no evidence block" in out


def test_evidence_for_another_commit_is_refused(gate):
    code, out = gate(reviews=[verdict(evidence={"commit": "a" * 39 + "b", "profile": "light"})])
    assert code == 1
    assert "not the submitted" in out


def test_evidence_carried_over_a_milestone_merge_passes_only_for_its_reviewer(gate):
    old = "c" * 40
    carry = {"reviewer": "codex", "carried_from": old, "carried_to": SHA, "paths": []}
    code, out = gate(
        packet(carried_reviews=[carry]), [verdict(evidence={"commit": old, "profile": "light"})]
    )
    assert code == 0, out
    code, out = gate(
        packet(carried_reviews=[{**carry, "reviewer": "opus"}]),
        [verdict(evidence={"commit": old, "profile": "light"})],
    )
    assert code == 1
    assert "not the submitted" in out


@pytest.mark.parametrize("bad", [{"clean": False}, {"result": "fail"}, {"commit": "b" * 40}])
def test_a_record_that_proves_nothing_is_refused(gate, bad):
    code, out = gate(packet(evidence=[record(**bad)]))
    assert code == 1
    assert "flo holds no passing clean light check" in out


def test_a_full_run_vouches_for_a_light_claim_but_not_the_reverse(gate):
    assert gate(packet(evidence=[record(profile="full")]))[0] == 0
    review = verdict(evidence={"commit": SHA, "profile": "full"})
    code, out = gate(reviews=[review])  # only a light record exists
    assert code == 1
    assert "no passing clean full check" in out


def test_ran_tests_false_still_fails_as_before(gate):
    code, out = gate(reviews=[verdict(ran_tests=False)])
    assert code == 1
    assert "0/1 reviews with ran_tests=true" in out


def test_a_story_started_before_the_flag_gates_as_it_always_did(gate):
    pkt = packet(evidence=[])
    del pkt["evidence_required"]
    review = verdict()
    del review["evidence"]
    assert gate(pkt, [review])[0] == 0  # the rollout path: only stories started after the change


def test_valid_evidence_does_not_stand_in_for_the_opus_final_review(gate):
    code, out = gate(packet(opus_final=True, tags=["money"]))
    assert code == 1
    assert OPUS_MESSAGE in out


def test_the_gate_records_how_long_it_took(gate, tmp_path):
    gate()
    assert isinstance(json.loads((tmp_path / "wt" / "task.json").read_text())["gate_seconds"], int)


def init_repo(path):
    path.mkdir()
    for args in (["init", "-q", "-b", "main"],):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    (path / ".gitignore").write_text("task.json\n")
    (path / "README").write_text("x\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "b"],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    wt = tmp_path / "repo"
    init_repo(wt)
    (wt / "task.json").write_text(json.dumps({"author": "sonnet"}))
    calls = []

    def fake_run_check(cwd, profile):
        calls.append(profile)
        F.LAST_STEPS.extend(
            [{"name": "ruff", "ok": True}, {"name": "pytest", "ok": fake_run_check.ok}]
        )
        return fake_run_check.ok

    fake_run_check.ok = True
    monkeypatch.setattr(F, "run_check", fake_run_check)
    return wt, fake_run_check, calls


def evidence_of(wt):
    return json.loads((wt / "task.json").read_text()).get("evidence", [])


def test_a_green_clean_run_leaves_a_passing_record_with_its_steps(repo):
    wt, _, _ = repo
    assert F.checked(wt, "light") is True
    (rec,) = evidence_of(wt)
    assert (rec["result"], rec["clean"], rec["profile"]) == ("pass", True, "light")
    assert rec["commit"] == F.git("rev-parse", "HEAD", cwd=wt)
    assert [s["name"] for s in rec["steps"]] == ["ruff", "pytest"]


def test_a_red_run_leaves_a_failing_record(repo):
    wt, fake, _ = repo
    fake.ok = False
    assert F.checked(wt, "light") is False
    (rec,) = evidence_of(wt)
    assert rec["result"] == "fail"


def test_a_dirty_tree_leaves_a_record_that_proves_nothing(repo):
    wt, _, _ = repo
    (wt / "README").write_text("changed\n")
    F.checked(wt, "light")
    (rec,) = evidence_of(wt)
    assert rec["clean"] is False
    assert (
        F.evidence_for(json.loads((wt / "task.json").read_text()), rec["commit"], "light") is None
    )


def test_a_cache_hit_on_a_new_commit_with_the_same_tree_still_gets_a_record(repo):
    wt, _, calls = repo
    F.checked(wt, "light")
    subprocess.run(
        [
            "git",
            "-C",
            str(wt),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "same tree, new commit",
        ],
        check=True,
        capture_output=True,
    )
    assert F.checked(wt, "light") is True
    assert calls == ["light"]  # the checks did not run again
    first, second = evidence_of(wt)
    assert second["commit"] != first["commit"]
    assert second["cached"] is True


def test_the_evidence_list_is_capped(repo):
    wt, _, _ = repo
    for n in range(60):
        F.record_evidence(wt / "task.json", record(commit=str(n)))
    assert len(evidence_of(wt)) == 50


@pytest.fixture
def start_world(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "design").mkdir(parents=True)
    (root / "design" / f"{SID}.md").write_text("packet\n")
    story = {
        "id": SID,
        "t": "T",
        "k": "chore",
        "s": "S",
        "tags": [],
        "reqs": ["SUP-005"],
        "dep": [],
        "st": "todo",
    }
    roadmap = {
        "milestones": [{"id": "M9", "name": "x", "branch": "milestone/M9"}],
        "epics": [{"id": "E00", "milestone": "M9", "stories": [story]}],
    }
    config = fleet()
    wt = tmp_path / "wt"

    def fake_git(*args, **kwargs):
        if args[:2] == ("worktree", "add"):
            wt.mkdir()
        return ""

    monkeypatch.setattr(F, "ROOT", root)
    monkeypatch.setattr(F, "worktree_path", lambda sid: wt)
    monkeypatch.setattr(F, "roadmap", lambda: roadmap)
    monkeypatch.setattr(F, "load_yaml", lambda path: config)
    monkeypatch.setattr(F, "state", lambda: {"spend": {}})
    monkeypatch.setattr(F, "free_accounts", lambda name, cfg: ["A"])
    monkeypatch.setattr(F, "git", fake_git)
    monkeypatch.setattr(F, "detect_session", lambda author, path: {"agent": author, "id": None})
    monkeypatch.setattr(F, "set_status", lambda sid, status: None)
    F.cmd_preflight([SID, "--reviewer", "qwen", "--verdict", "pass"])
    return wt


def test_a_story_started_now_requires_evidence(start_world):
    F.cmd_start([SID])
    pkt = json.loads((start_world / "task.json").read_text())
    assert pkt["evidence_required"] is True
    assert "evidence" not in pkt  # nothing is on record until a check runs


@pytest.fixture
def review_packet(tmp_path, monkeypatch, capsys):
    wt = tmp_path / "wt"
    wt.mkdir()
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / f"{SID}.md").write_text("packet\n")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.setattr(F, "worktree_path", lambda sid: wt)
    monkeypatch.setattr(F, "git", lambda *a, **k: "")

    def render(**over):
        pkt = {
            "title": "T",
            "kind": "chore",
            "size": "S",
            "tags": [],
            "requirements": ["SUP-005"],
            "branch": "story/x",
            "milestone_branch": "milestone/M9",
            "opus_final": False,
            "design": f"design/{SID}.md",
            "submitted_sha": SHA,
            "evidence": [record()],
        }
        pkt.update(over)
        (wt / "task.json").write_text(json.dumps(pkt))
        F.cmd_review([SID])
        return capsys.readouterr().out

    return render


def test_the_review_prompt_names_the_commit_to_cite_and_the_record_flo_holds(review_packet):
    out = review_packet()
    assert f'"evidence": {{"commit": "{SHA}", "profile": "light"}}' in out
    assert f"flo already holds a passing, clean light check for {SHA[:10]}" in out


def test_the_review_prompt_says_so_when_flo_holds_no_record(review_packet):
    out = review_packet(evidence=[])
    assert f"flo holds no passing light check for {SHA[:10]} yet" in out


def test_the_real_run_check_reports_each_step_it_ran(tmp_path, monkeypatch):
    monkeypatch.setattr(
        F, "CHECKS", [("passes", ["true"]), ("fails", ["false"]), ("never", ["true"])]
    )
    F.LAST_STEPS.clear()
    assert F.run_check(tmp_path, "full") is False
    assert F.LAST_STEPS == [{"name": "passes", "ok": True}, {"name": "fails", "ok": False}]
