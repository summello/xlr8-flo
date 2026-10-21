"""The rules `flo facts` and `flo round` read a story's files by (agents/scripts/flo_facts.py).

The fleet supervisor (~/.claude/fleet) acts on these facts with no other knowledge of the
repository, so a wrong reading here starts the wrong step: a limited author marked failed waits
for the operator instead of resuming, and a verdict on an old commit read as fresh merges code
nobody reviewed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[4] / "agents" / "scripts"
spec = importlib.util.spec_from_file_location("flo_facts", SCRIPTS / "flo_facts.py")
assert spec is not None and spec.loader is not None
F = importlib.util.module_from_spec(spec)
sys.modules["flo_facts"] = F
spec.loader.exec_module(F)


@pytest.mark.parametrize(
    ("log", "result"),
    [
        (None, "none"),
        ("working on it", "running"),
        ("done\nexit 0\n", "ok"),
        ("boom\nexit 1\n", "failed"),
        ("You've hit your session limit · resets 7:30pm\nexit 1\n",
         "limit"),
        ("getaddrinfo ENOTFOUND api.anthropic.com\nexit 1\n", "limit"),
        # this product has rate-limiting stories: an author reporting on one is not limited
        ("Added the edge rate limit for login; usage limit docs updated.\nexit 0\n", "ok"),
        ("FAILED: the rate limit header is missing; usage limit not enforced\nexit 1\n", "failed"),
        # only the last lines count: an old limit message far up the log is history
        ("You've hit your session limit\n" + "x" * 700 + "\nexit 1\n", "failed"),
        ("first run\nexit 1\nresumed\nexit 0\n", "ok"),
    ],
)
def test_author_result(log: str | None, result: str) -> None:
    assert F.author_result(log) == result


@pytest.mark.parametrize(
    ("review", "reviewed", "submitted", "state"),
    [
        (None, None, "s1", "missing"),
        ({"verdict": "approve"}, "s1", "s1", "approve"),
        ({"verdict": "approved"}, "s1", "s1", "approve"),
        ({"verdict": "changes_requested"}, "s1", "s1", "changes"),
        # a verdict on an earlier commit is stale even when it approved
        ({"verdict": "approve"}, "s0", "s1", "stale"),
        ({"verdict": "approve"}, None, "s1", "stale"),
        ({"verdict": "approve"}, "s1", None, "stale"),
        ({"verdict": "lgtm!"}, "s1", "s1", "malformed"),
    ],
)
def test_verdict_state(
    review: dict | None, reviewed: str | None, submitted: str | None, state: str
) -> None:
    assert F.verdict_state(review, reviewed, submitted) == state


def test_rounds_done_counts_only_this_storys_round_files() -> None:
    names = ["E1-S1.round1.md", "E1-S1.round2.md", "E1-S10.round5.md",
             "E1-S1.rechain.md", "E1-S1.log"]
    assert F.rounds_done(names, "E1-S1") == 2
    assert F.rounds_done(names, "E1-S10") == 5
    assert F.rounds_done(names, "E2-S1") == 0


def test_round_file_sends_changes_requested_findings_most_severe_first() -> None:
    verdicts = {
        "codex": {"verdict": "changes_requested", "findings": [
            {"severity": "minor", "file": "a.py", "line": 3, "problem": "naming"},
            {"severity": "blocker", "file": "b.py", "line": 9, "requirement": "FIN-006",
             "problem": "no lock", "fix": "lock first"},
        ]},
        "opus": {"verdict": "approve", "findings": [
            {"severity": "major", "file": "c.py", "problem": "approver's note"},
        ]},
    }
    text = F.round_file("E1-S1", 2, verdicts)
    assert text.startswith("REVIEW ROUND 2 for E1-S1.")
    body = text.split("\n\n", 1)[1]
    assert body.index("no lock") < body.index("naming")
    assert "(blocker, FIN-006, codex) b.py:9: no lock" in body and "Fix: lock first" in body
    assert "approver's note" not in text
    assert "No submit, push or merge." in text
