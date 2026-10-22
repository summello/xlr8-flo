"""The fleet supervisor's decision rules (E23-S11).

agents/fleet/fleet.py decides which step runs next for every story, within the Claude and Codex
windows. A wrong rule launches a duplicate author, resumes into a closed window, or merges nothing
while the operator sees no reason. The rules are pure functions; these tests pin each branch.
"""

from __future__ import annotations

import json
import sys
from importlib import machinery, util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[4] / "agents" / "fleet" / "fleet.py"
loader = machinery.SourceFileLoader("fleet_supervisor", str(PATH))
spec = util.spec_from_loader("fleet_supervisor", loader)
assert spec is not None
S = util.module_from_spec(spec)
sys.modules["fleet_supervisor"] = S
loader.exec_module(S)


def row(account, five, week=80, stale=False):
    out = {"account": account, "five": {"leftPct": five}, "week": {"leftPct": week}}
    if stale:
        out["stale"] = True
    return out


def snapshot(a=80, b=80, codex=80):
    return {
        "limits": {"claude": [row("A", a), row("B", b)]},
        "codex": {"five": {"leftPct": codex}, "week": {"leftPct": 80}},
    }


def story(**over):
    base = {
        "id": "S1",
        "author": "sonnet",
        "account": "A",
        "gated": False,
        "blocked": False,
        "merged": False,
        "rounds": 0,
        "running": [],
        "author_result": "ok",
        "head": "h1",
        "submitted_sha": "h1",
        "verdicts": {"codex": "approve"},
        "ci_red": False,
        "ci": None,
    }
    base.update(over)
    return base


def ctx(cap=None, **over):
    base = {
        "cap": cap or S.capacity(snapshot(), {}),
        "busy": {},
        "results": {},
        "line_stopped": False,
        "max_rounds": 3,
        "accounts": ["A", "B"],
        "allow_start": True,
    }
    base.update(over)
    return base


def steps(actions):
    return [(a.get("step"), a.get("reviewer"), a.get("account")) for a in actions if "step" in a]


# capacity ------------------------------------------------------------------------------------


def test_unknown_limits_allow_work():
    cap = S.capacity(None, {})
    assert cap["known"] is False
    assert S.can(cap, "start", "A") and S.can(cap, "continue", "B")


def test_a_stale_row_is_unknown_not_zero():
    cap = S.capacity({"limits": {"claude": [row("A", 0, 0, stale=True)]}}, {})
    assert S.can(cap, "start", "A") is True


def test_start_and_continue_thresholds_differ():
    cap = S.capacity(snapshot(a=24, b=9), {})  # defaults: start 25/15, continue 10/5
    assert (cap["start"]["A"], cap["continue"]["A"]) == (False, True)
    assert (cap["start"]["B"], cap["continue"]["B"]) == (False, False)


def test_the_operator_can_disable_an_account():
    cap = S.capacity(snapshot(), {"disabled_accounts": ["B"]})
    assert cap["start"]["B"] is False and cap["continue"]["B"] is False
    assert cap["start"]["A"] is True


def test_codex_needs_both_its_windows():
    assert S.capacity(snapshot(codex=19), {})["codex"] is False
    assert S.capacity(snapshot(codex=20), {})["codex"] is True


def test_author_can_uses_codex_windows_for_codex_and_the_account_for_claude():
    cap = S.capacity(snapshot(a=5, codex=19), {})
    assert S.author_can({"author": "codex"}, cap) is False
    assert S.author_can({"author": "sonnet", "account": "A"}, cap) is False
    assert S.author_can({"author": "sonnet", "account": "B"}, cap) is True
    assert S.author_can({"author": "opencode-nemotron", "account": None}, cap) is True


def test_opus_review_runs_on_the_account_with_the_most_five_hour_headroom():
    assert S.opus_account(S.capacity(snapshot(a=30, b=70), {}), ["A", "B"]) == "B"
    assert S.opus_account(S.capacity(snapshot(a=5, b=5), {}), ["A", "B"]) is None


# decide --------------------------------------------------------------------------------------


def test_a_running_or_busy_story_waits():
    assert S.decide(story(running=["author"]), ctx()) == []
    assert S.decide(story(), ctx(busy={"S1": {"submit"}})) == []


def test_a_merged_story_with_red_ci_is_fixed_and_a_green_one_is_left():
    assert steps(S.decide(story(merged=True, ci_red=True), ctx())) == [("fix", None, "A")]
    assert S.decide(story(merged=True), ctx()) == []


def test_a_merged_story_gets_one_fix_after_its_last_submit_then_the_operator():
    """A merged story is never resubmitted, so its red stays red after the fix ran; E22-S12's
    author was relaunched every tick on 22 Oct."""
    red = story(merged=True, ci_red=True)
    after = {("S1", "submit"): {"started": 100, "exit": 0}, ("S1", "fix"): {"started": 200, "exit": 0}}
    (act,) = S.decide(red, ctx(results=after))
    assert act["need"].startswith("a CI fix already ran on this merged story")
    before = {**after, ("S1", "fix"): {"started": 50, "exit": 0}}
    assert steps(S.decide(red, ctx(results=before))) == [("fix", None, "A")]
    killed = {("S1", "fix"): {"started": 9, "exit": 143}}  # no recorded submit: any finished fix
    assert "need" in S.decide(red, ctx(results=killed))[0]


def test_a_blocked_story_asks_the_operator():
    (act,) = S.decide(story(blocked=True), ctx())
    assert act["need"].startswith("blocked")


@pytest.mark.parametrize("result", ["limit", "none"])
def test_a_stopped_author_resumes_only_when_its_account_may_continue(result):
    assert steps(S.decide(story(author_result=result), ctx())) == [("resume", None, "A")]
    cap = S.capacity(snapshot(a=5), {})
    assert S.decide(story(author_result=result), ctx(cap)) == []


def test_a_failed_author_asks_the_operator():
    (act,) = S.decide(story(author_result="failed"), ctx())
    assert "reassign" in act["need"]


def test_red_ci_on_the_submitted_head_runs_a_fix():
    assert steps(S.decide(story(ci_red=True), ctx())) == [("fix", None, "A")]


def test_a_fix_that_committed_nothing_asks_instead_of_looping():
    results = {("S1", "fix"): {"head": "h1", "exit": 0}}
    (act,) = S.decide(story(ci_red=True), ctx(results=results))
    assert "committed nothing" in act["need"]


def test_an_unsubmitted_head_is_submitted_even_when_ci_was_red_on_the_old_commit():
    out = S.decide(story(head="h2", ci_red=True), ctx())
    assert steps(out) == [("submit", None, None)]


def test_a_submit_that_failed_on_this_commit_asks_the_operator():
    results = {("S1", "submit"): {"head": "h2", "exit": 1}}
    (act,) = S.decide(story(head="h2"), ctx(results=results))
    assert "submit failed" in act["need"]


def test_a_malformed_verdict_asks_the_operator():
    (act,) = S.decide(story(verdicts={"codex": "malformed"}), ctx())
    assert "malformed" in act["need"]


def test_missing_reviewers_are_launched_codex_only_with_its_windows_open():
    verdicts = {"codex": "missing", "qwen": "stale"}
    assert steps(S.decide(story(verdicts=verdicts), ctx())) == [
        ("review", "codex", ""),
        ("review", "qwen", ""),
    ]
    closed = S.capacity(snapshot(codex=5), {})
    assert steps(S.decide(story(verdicts=verdicts), ctx(closed))) == [("review", "qwen", "")]


def test_a_missing_opus_review_runs_on_the_roomiest_account_or_waits():
    verdicts = {"codex": "approve", "opus": "missing"}
    out = S.decide(story(verdicts=verdicts, gated=True), ctx(S.capacity(snapshot(a=30, b=70), {})))
    assert steps(out) == [("review", "opus", "B")]
    assert S.decide(story(verdicts=verdicts), ctx(S.capacity(snapshot(a=5, b=5), {}))) == []


def test_changes_requested_runs_a_round_until_the_cap_then_asks_for_arbitration():
    changes = {"codex": "changes"}
    assert steps(S.decide(story(verdicts=changes, rounds=2), ctx())) == [("round", None, "A")]
    (act,) = S.decide(story(verdicts=changes, rounds=3), ctx())
    assert "flo escalate S1" in act["need"]


def test_a_stopped_line_stops_every_merge():
    assert S.decide(story(), ctx(line_stopped=True)) == []


def test_a_gated_story_finishes_only_on_green_story_ci():
    assert S.decide(story(gated=True, ci="pending"), ctx()) == []
    assert steps(S.decide(story(gated=True, ci="green"), ctx())) == [("finish", None, None)]


def test_an_ungated_approved_story_finishes_and_a_failed_finish_asks():
    assert steps(S.decide(story(), ctx())) == [("finish", None, None)]
    results = {("S1", "finish"): {"head": "h1", "exit": 1}}
    (act,) = S.decide(story(), ctx(results=results))
    assert "finish failed" in act["need"]


# plan ----------------------------------------------------------------------------------------


def facts(**over):
    base = {
        "stories": [],
        "in_flight": 1,
        "max_in_flight": 6,
        "free_accounts": ["A", "B"],
        "next": ["N1", "N2"],
    }
    base.update(over)
    return base


def test_plan_starts_one_story_on_the_first_account_with_start_room():
    assert S.plan(facts(), ctx()) == [{"step": "begin", "story": "N1", "account": "A"}]
    cap = S.capacity(snapshot(a=20, b=80), {})  # A is below the start threshold
    assert S.plan(facts(), ctx(cap)) == [{"step": "begin", "story": "N1", "account": "B"}]


def test_plan_starts_with_no_account_when_no_claude_slot_is_tracked():
    assert S.plan(facts(free_accounts=[]), ctx()) == [
        {"step": "begin", "story": "N1", "account": ""}
    ]


@pytest.mark.parametrize(
    "over",
    [{"line_stopped": True}, {"allow_start": False}],
)
def test_plan_starts_nothing_when_the_line_is_stopped_or_starts_are_off(over):
    assert S.plan(facts(), ctx(**over)) == []


def test_plan_starts_nothing_when_the_fleet_is_full():
    assert S.plan(facts(in_flight=6), ctx()) == []


def test_a_skipped_start_is_explained_by_a_note_and_never_launched():
    cap = S.capacity(snapshot(a=5, b=5), {})
    out = S.plan(facts(), ctx(cap))
    assert out == [{"note": "start blocked: no Claude account has room (next: N1)"}]


def test_no_note_when_nothing_was_ready_or_the_start_went_ahead():
    cap = S.capacity(snapshot(a=5, b=5), {})
    assert S.plan(facts(next=[]), ctx(cap)) == []
    assert all("note" not in a for a in S.plan(facts(), ctx()))
    assert S.plan(facts(), ctx(cap, line_stopped=True)) == []  # not even a note


def test_nothing_startable_because_no_pre_flight_record_asks_the_operator_once_per_story():
    out = S.plan(facts(next=[], needs_preflight=["N1", "N2"]), ctx())
    assert out == [
        {
            "need": "nothing can start: N1 and others have no pre-flight record; run flo preflight",
            "story": "N1",
        }
    ]


def test_no_pre_flight_need_when_a_story_can_start_or_starts_are_off():
    assert all("need" not in a for a in S.plan(facts(needs_preflight=["N9"]), ctx()))
    assert S.plan(facts(next=[], needs_preflight=["N1"]), ctx(allow_start=False)) == []
    assert S.plan(facts(next=[], needs_preflight=["N1"]), ctx(line_stopped=True)) == []
    assert S.plan(facts(next=[], needs_preflight=["N1"], in_flight=6), ctx()) == []


# heavy_slots ---------------------------------------------------------------------------------


def test_heavy_steps_beyond_the_limit_wait_for_the_next_tick():
    acts = [{"step": "begin"}, {"step": "submit"}, {"step": "review"}, {"step": "finish"}]
    kept = S.heavy_slots(acts, busy_heavy=1, limit=2)
    assert [a["step"] for a in kept] == ["begin", "review"]
    assert S.heavy_slots([{"note": "x"}], busy_heavy=5, limit=1) == [{"note": "x"}]


# tick ----------------------------------------------------------------------------------------


@pytest.fixture
def runner(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(S, "STATE", state)
    run = tmp_path / "runner"
    run.mkdir()
    launched, notified = [], []
    monkeypatch.setattr(S, "launch", lambda cfg, d, act, head: launched.append(act))
    monkeypatch.setattr(S, "notify", lambda title, text: notified.append((title, text)))
    cfg = {
        "_path": "x",
        "project": {"name": "t", "runner": str(run), "facts": "cat facts.json"},
        "steps": {"begin": "echo {story}"},
        "limits": {},
    }

    def write(facts_doc, limits_doc=None):
        (run / "facts.json").write_text(json.dumps(facts_doc))
        if limits_doc is not None:
            (run / "limits.json").write_text(json.dumps(limits_doc))
            cfg["limits"] = {"command": "cat limits.json"}

    return cfg, write, launched, notified, state / "t"


def test_a_held_tick_lock_makes_a_second_tick_do_nothing(runner):
    cfg, write, launched, _, state = runner
    write(facts())
    (state / "tick.lock").mkdir(parents=True)
    status = S.tick(cfg)
    assert "another tick is running" in status["notes"]
    assert launched == []


def test_a_dry_run_plans_and_launches_nothing(runner):
    cfg, write, launched, _, _ = runner
    write(facts())
    status = S.tick(cfg, dry=True)
    assert status["actions"] == [{"step": "begin", "story": "N1", "account": "A"}]
    assert launched == []


def test_a_skipped_start_lands_in_notes_not_actions(runner):
    cfg, write, launched, _, state = runner
    write(facts(), snapshot(a=5, b=5))
    status = S.tick(cfg)
    assert status["actions"] == [] and launched == []
    assert status["notes"] == ["start blocked: no Claude account has room (next: N1)"]
    assert "start blocked" in (state / "tick.log").read_text()


def test_the_pre_flight_need_notifies_once(runner):
    cfg, write, launched, notified, state = runner
    write(facts(next=[], needs_preflight=["N1"]))
    first = S.tick(cfg)
    S.tick(cfg)
    assert first["needs"][0]["story"] == "N1"
    assert len(notified) == 1 and launched == []
    assert (state / "notified.json").exists()
