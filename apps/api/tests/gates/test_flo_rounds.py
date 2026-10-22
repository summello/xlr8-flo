"""The scorecard's `rounds` come from the round files, void rounds are not charged, and records
that predate this are marked, not rewritten (E23-S13).

Before this, `rounds` was a counter that only `flo escalate` ever wrote: six records that carry the
observed review-run counts said 1 round while their reviewers ran 1 to 7 times.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.gates.flo_loader import SCRIPTS, load_flo

spec = importlib.util.spec_from_file_location("flo_facts", SCRIPTS / "flo_facts.py")
assert spec is not None and spec.loader is not None
FACTS = importlib.util.module_from_spec(spec)
sys.modules.setdefault("flo_facts", FACTS)
spec.loader.exec_module(FACTS)
F = load_flo()
SID = "E00-S01"
REPO = Path(__file__).resolve().parents[4]

FINDING = {"severity": "major", "file": "a.py", "line": 3, "problem": "wrong", "fix": "right"}


# void rounds ---------------------------------------------------------------------------------


def test_a_round_file_with_a_finding_is_not_void_and_an_empty_one_is():
    full = FACTS.round_file(
        SID, 1, {"codex": {"verdict": "changes_requested", "findings": [FINDING]}}
    )
    empty = FACTS.round_file(SID, 2, {"codex": {"verdict": "changes_requested", "findings": []}})
    assert FACTS.is_void_round(full) is False
    assert FACTS.is_void_round(empty) is True
    assert FACTS.void_rounds([full, empty, empty]) == 2


# record_score --------------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path, monkeypatch):
    root, wt = tmp_path / "root", tmp_path / "wt"
    (root / ".scratch").mkdir(parents=True)
    (wt / "reviews").mkdir(parents=True)
    scorecard = tmp_path / "scorecard.json"
    old = {
        "story": "OLD-1",
        "kind": "crud",
        "size": "S",
        "tags": [],
        "author": "codex",
        "rounds": 3,
    }
    scorecard.write_text(json.dumps({"min_sample": 3, "stories": [old]}))
    monkeypatch.setattr(F, "ROOT", root)
    monkeypatch.setattr(F, "SCORECARD", scorecard)
    monkeypatch.setattr(F, "worktree_path", lambda sid: wt)
    monkeypatch.setattr(F, "detect_session", lambda who, path: {"agent": who, "id": None})
    review = {"story": SID, "reviewer": "codex", "verdict": "approve", "ran_tests": True,
              "findings": []}  # fmt: skip
    (wt / "reviews" / f"{SID}.codex.json").write_text(json.dumps(review))
    return root, scorecard


def write_round(root, n, findings):
    verdicts = {"codex": {"verdict": "changes_requested", "findings": findings}}
    (root / ".scratch" / f"{SID}.round{n}.md").write_text(FACTS.round_file(SID, n, verdicts))


def record(escalations=0):
    story = {"k": "chore", "s": "S", "tags": [], "t": "T"}
    pkt = {"author": "sonnet", "opus_final": False, "rounds": escalations}
    F.record_score(SID, pkt, story)


def last(scorecard):
    return json.loads(scorecard.read_text())["stories"][-1]


def test_rounds_are_the_round_files_with_findings_plus_one(world):
    root, scorecard = world
    write_round(root, 1, [FINDING])
    write_round(root, 2, [FINDING, FINDING])
    record()
    rec = last(scorecard)
    assert (rec["rounds"], rec["void_rounds"]) == (3, 0)


def test_a_void_round_is_counted_apart_and_not_charged(world):
    root, scorecard = world
    write_round(root, 1, [FINDING])
    write_round(root, 2, [])  # a reviewer that hit its cap: nothing to fix
    record()
    rec = last(scorecard)
    assert (rec["rounds"], rec["void_rounds"]) == (2, 1)


def test_no_round_files_is_one_round(world):
    _, scorecard = world
    record()
    assert last(scorecard)["rounds"] == 1


def test_another_storys_round_files_are_not_counted(world):
    root, scorecard = world
    (root / ".scratch" / "E00-S10.round1.md").write_text("REVIEW ROUND 1\n1. (major) x\n")
    record()
    assert last(scorecard)["rounds"] == 1


def test_escalations_review_runs_and_the_basis_marker_are_recorded(world):
    _, scorecard = world
    record(escalations=2)
    rec = last(scorecard)
    assert rec["escalations"] == 2
    assert rec["review_runs"] == {"codex": 1}
    assert rec["rounds_basis"] == "round_files"


def test_an_existing_record_is_not_rewritten(world):
    _, scorecard = world
    before = json.loads(scorecard.read_text())["stories"][0]
    record()
    assert json.loads(scorecard.read_text())["stories"][0] == before


# rendering -----------------------------------------------------------------------------------


def rec(story, author, basis=None, rounds=1):
    base = {"story": story, "kind": "chore", "size": "S", "tags": [], "author": author,
            "reviewers": ["codex"], "rounds": rounds, "gate_failures": 0, "opus_final": False,
            "escapes": 0, "review": {"codex": {"ran_tests": True, "findings": 1, "blockers": 0,
            "blockers_dismissed": 0}}}  # fmt: skip
    if basis:
        base["rounds_basis"] = basis
    return base


def render(monkeypatch, records):
    monkeypatch.setattr(F, "_SCORES_OVERRIDE", {"min_sample": 3, "stories": records})
    return F.render_scores()


def test_legacy_records_get_a_data_quality_notice_and_a_verified_column(monkeypatch):
    records = [rec("A", "sonnet"), rec("B", "sonnet"), rec("C", "sonnet", "round_files")]
    out = render(monkeypatch, records)
    assert "Data quality" in out
    assert "2 of 3 records" in out
    assert "| Rounds verified |" in out
    assert "| `sonnet` | 3 | 1/3 |" in out


def test_no_notice_when_every_record_is_verified(monkeypatch):
    out = render(monkeypatch, [rec(s, "sonnet", "round_files") for s in "ABC"])
    assert "Data quality" not in out
    assert "| `sonnet` | 3 | 3/3 |" in out


def test_the_committed_scorecard_still_has_no_verified_records_and_says_so():
    text = (REPO / "agents" / "SCORECARD.md").read_text()
    assert "Data quality" in text and "0/29" in text
