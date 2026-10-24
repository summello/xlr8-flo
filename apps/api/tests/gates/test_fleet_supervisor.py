"""The fleet supervisor's decision rules (E23-S11).

agents/fleet/fleet.py decides which step runs next for every story, within the Claude and Codex
windows. A wrong rule launches a duplicate author, resumes into a closed window, or merges nothing
while the operator sees no reason. The rules are pure functions; these tests pin each branch.
"""

from __future__ import annotations

import json
import sys
import time
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
    assert S.decide(story(), ctx(busy={"S1": {("submit", "")}})) == []


def test_a_merged_story_with_red_ci_is_fixed_and_a_green_one_is_left():
    assert steps(S.decide(story(merged=True, ci_red=True), ctx())) == [("fix", None, "A")]
    assert S.decide(story(merged=True), ctx()) == []


def test_a_merged_story_gets_one_fix_after_its_last_submit_then_the_operator():
    """A merged story is never resubmitted, so its red stays red after the fix ran; E22-S12's
    author was relaunched every tick on 22 Oct."""
    red = story(merged=True, ci_red=True)
    after = {
        ("S1", "submit", ""): {"started": 100, "exit": 0},
        ("S1", "fix", ""): {"started": 200, "exit": 0},
    }
    (act,) = S.decide(red, ctx(results=after))
    assert act["need"].startswith("a CI fix already ran on this merged story")
    before = {**after, ("S1", "fix", ""): {"started": 50, "exit": 0}}
    assert steps(S.decide(red, ctx(results=before))) == [("fix", None, "A")]
    killed = {("S1", "fix", ""): {"started": 9, "exit": 143}}  # no submit: any finished fix
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
    results = {("S1", "fix", ""): {"head": "h1", "exit": 0}}
    (act,) = S.decide(story(ci_red=True), ctx(results=results))
    assert "committed nothing" in act["need"]


def test_an_unsubmitted_head_is_submitted_even_when_ci_was_red_on_the_old_commit():
    out = S.decide(story(head="h2", ci_red=True), ctx())
    assert steps(out) == [("submit", None, None)]


def test_a_submit_that_failed_on_this_commit_asks_the_operator():
    results = {("S1", "submit", ""): {"head": "h2", "exit": 1}}
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
    results = {("S1", "finish", ""): {"head": "h1", "exit": 1}}
    (act,) = S.decide(story(), ctx(results=results))
    assert "finish failed" in act["need"]


def red_line(*jobs, sha="m1"):
    return {"line_stopped": True, "line_jobs": {"M3": {"sha": sha, "jobs": list(jobs)}}}


def test_a_story_red_on_the_milestones_jobs_is_fixed_once_on_the_milestone():
    s = story(ci_red=True, milestone="M3", ci_jobs=["frontend-static"])
    (act,) = S.decide(s, ctx(**red_line("frontend-static", "backend")))
    assert act["need"] == "milestone red: fix it once on the milestone"
    merged = story(merged=True, ci_red=True, milestone="M3", ci_jobs=["frontend-static"])
    assert "milestone red" in S.decide(merged, ctx(**red_line("frontend-static")))[0]["need"]


def test_disjoint_jobs_or_a_green_milestone_keep_the_story_fix():
    s = story(ci_red=True, milestone="M3", ci_jobs=["backend"])
    assert steps(S.decide(s, ctx(**red_line("frontend-static")))) == [("fix", None, "A")]
    assert steps(S.decide(s, ctx())) == [("fix", None, "A")]


def test_several_stories_on_one_milestone_run_raise_one_need_whatever_jobs_overlap():
    a = story(id="S1", ci_red=True, milestone="M3", ci_jobs=["frontend-static"])
    b = story(id="S2", ci_red=True, milestone="M3", ci_jobs=["backend"])
    acts = S.plan(facts(stories=[a, b]), ctx(**red_line("frontend-static", "backend")))
    assert [x["story"] for x in acts if "need" in x] == ["S1"]


def test_another_milestones_red_does_not_suppress_a_story_fix():
    s = story(ci_red=True, milestone="M1", ci_jobs=["frontend-static"])
    c = ctx(**red_line("frontend-static"))  # red_line names M3 only
    assert steps(S.decide(s, c)) == [("fix", None, "A")]


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


# one run record per reviewer, one reviewer at a time (E23-S14) ---------------------------------


def gated_facts():
    return facts(stories=[story(gated=True, verdicts={"codex": "missing", "opus": "missing"})])


def test_a_gated_story_with_both_verdicts_missing_gets_one_review_per_tick_codex_first():
    (act,) = [a for a in S.plan(gated_facts(), ctx()) if a.get("step") == "review"]
    assert act["reviewer"] == "codex"


def test_the_next_reviewer_starts_after_the_first_exits_and_not_while_it_runs():
    f = gated_facts()
    f["stories"][0]["verdicts"] = {"codex": "approve", "opus": "missing"}
    (act,) = [a for a in S.plan(f, ctx()) if a.get("step") == "review"]
    assert act["reviewer"] == "opus"
    third = S.plan(f, ctx(busy={"S1": {("review", "opus")}}))
    assert [a for a in third if a.get("story") == "S1"] == []


def test_each_reviewer_gets_its_own_run_record_and_both_are_tracked_in_busy(tmp_path, monkeypatch):
    class Proc:
        pid = 4242

    monkeypatch.setattr(S.subprocess, "Popen", lambda *a, **k: Proc())
    monkeypatch.setattr(S, "lstart", lambda pid: "Thu Oct 23 10:00:00 2026")
    monkeypatch.setattr(S, "alive", lambda pid, start: True)
    cfg = {"project": {"runner": str(tmp_path)}, "steps": {"review": "echo {reviewer}"}}
    (tmp_path / "runs").mkdir()
    for who in ("codex", "opus"):
        S.launch(cfg, tmp_path, {"story": "S1", "step": "review", "reviewer": who}, "h1")
    assert sorted(p.name for p in (tmp_path / "runs").glob("*.json")) == [
        "S1.review.codex.json",
        "S1.review.opus.json",
    ]
    busy, results = S.records(tmp_path)
    assert busy == {"S1": {("review", "codex"), ("review", "opus")}}
    assert results[("S1", "review", "codex")]["reviewer"] == "codex"


def test_an_old_review_record_without_a_reviewer_never_blocks_a_new_run(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "alive", lambda pid, start: True)
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "S1.review.json").write_text(
        json.dumps(
            {"story": "S1", "step": "review", "pid": 1, "start": "x", "started": 1, "head": "h"}
        )
    )
    busy, results = S.records(tmp_path)
    assert busy == {}
    assert ("S1", "review", "") in results


# recoverable records, process identity, network backoff, reconciliation (E23-S17) -----------------

START = "Thu Oct 23 10:00:00 2026"


class Boom(Exception):
    pass


def cfg_for(tmp_path):
    (tmp_path / "runs").mkdir(exist_ok=True)
    return {"project": {"runner": str(tmp_path)}, "steps": {"submit": "echo {story}"}}


def test_the_record_is_complete_on_disk_before_the_process_starts(tmp_path, monkeypatch):
    seen = {}

    def popen(*a, **k):
        seen["rec"] = json.loads((tmp_path / "runs" / "S1.submit.json").read_text())
        raise Boom

    monkeypatch.setattr(S.subprocess, "Popen", popen)
    with pytest.raises(Boom):
        S.launch(cfg_for(tmp_path), tmp_path, {"story": "S1", "step": "submit"}, "h1")
    rec = seen["rec"]
    assert rec["state"] == "launching" and rec["pid"] is None and rec["start"] is None
    assert rec["head"] == "h1" and rec["attempt"] == 1 and rec["cmd"] and rec["started"]
    assert not list((tmp_path / "runs").glob("*.tmp"))
    # the visible record survives the failed launch, and nothing is running: a dead attempt
    monkeypatch.setattr(S, "find_running", lambda ex: None)
    busy, results = S.records(tmp_path)
    assert busy == {} and results[("S1", "submit", "")]["exit"] == -1


def test_a_launch_stores_pid_and_start_identity(tmp_path, monkeypatch):
    class Proc:
        pid = 77

    monkeypatch.setattr(S.subprocess, "Popen", lambda *a, **k: Proc())
    monkeypatch.setattr(S, "lstart", lambda pid: START)
    S.launch(cfg_for(tmp_path), tmp_path, {"story": "S1", "step": "submit"}, "h1")
    rec = json.loads((tmp_path / "runs" / "S1.submit.json").read_text())
    assert (rec["state"], rec["pid"], rec["start"]) == ("running", 77, START)


def test_a_crash_between_popen_and_the_second_write_adopts_the_live_process(tmp_path, monkeypatch):
    class Proc:
        pid = 77

    monkeypatch.setattr(S.subprocess, "Popen", lambda *a, **k: Proc())

    def die(pid):
        raise OSError("disk")

    monkeypatch.setattr(S, "lstart", die)
    S.launch(
        cfg_for(tmp_path), tmp_path, {"story": "S1", "step": "submit"}, "h1"
    )  # second write fails
    assert json.loads((tmp_path / "runs" / "S1.submit.json").read_text())["state"] == "launching"
    monkeypatch.setattr(S, "find_running", lambda ex: (77, START))
    monkeypatch.setattr(S, "lstart", lambda pid: START)
    busy, _ = S.records(tmp_path)
    assert busy == {"S1": {("submit", "")}}  # busy, so the story is not launched twice


def write_run(tmp_path, **over):
    (tmp_path / "runs").mkdir(exist_ok=True)
    rec = {"story": "S1", "step": "submit", "pid": 99, "start": START, "started": 1, "head": "h"}
    rec.update(over)
    (tmp_path / "runs" / "S1.submit.json").write_text(json.dumps(rec))


def test_a_live_pid_is_busy_only_when_its_start_time_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "find_running", lambda ex: None)
    write_run(tmp_path)
    monkeypatch.setattr(S, "lstart", lambda pid: START)
    assert S.records(tmp_path)[0] == {"S1": {("submit", "")}}
    monkeypatch.setattr(S, "lstart", lambda pid: "Fri Oct 24 09:00:00 2026")  # the pid was reused
    busy, results = S.records(tmp_path)
    assert busy == {} and results[("S1", "submit", "")]["exit"] == -1
    monkeypatch.setattr(S, "lstart", lambda pid: None)  # ps failed: dead
    assert S.records(tmp_path)[0] == {}


def test_an_old_record_without_process_identity_cannot_block_a_story_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "find_running", lambda ex: None)
    monkeypatch.setattr(S, "lstart", lambda pid: START)
    write_run(tmp_path, start=None)
    busy, results = S.records(tmp_path)
    assert busy == {} and results[("S1", "submit", "")]["exit"] == -1


def test_a_partial_tmp_file_is_never_read_as_a_record(tmp_path, monkeypatch):
    write_run(tmp_path, start=None)
    (tmp_path / "runs" / "S2.submit.json.tmp").write_text('{"story": "S2", "st')
    monkeypatch.setattr(S, "find_running", lambda ex: None)
    _, results = S.records(tmp_path)
    assert list(results) == [("S1", "submit", "")]


def net(**over):
    return story(author_result="network", head="h1", author_ended=1000, **over)


def test_network_retries_wait_1_5_and_15_minutes_then_ask_the_operator():
    def at(now, tries):
        res = (
            {("S1", "resume", ""): {"kind": "network", "attempt": tries, "head": "h1", "exit": 1}}
            if tries
            else {}
        )
        return S.decide(net(), ctx(now=now, results=res))

    assert at(1059, 0) == []  # no early launch
    (a,) = at(1060, 0)
    assert (a["step"], a["kind"], a["attempt"], a["retry_at"]) == ("resume", "network", 1, 1060)
    assert at(1299, 1) == [] and at(1300, 1)[0]["attempt"] == 2
    assert at(1899, 2) == [] and at(1900, 2)[0]["attempt"] == 3
    (n,) = at(99999, 3)
    assert "need" in n and "step" not in n


def test_a_new_head_restarts_the_network_schedule():
    res = {("S1", "resume", ""): {"kind": "network", "attempt": 3, "head": "old", "exit": 1}}
    assert S.decide(net(), ctx(now=1060, results=res))[0]["attempt"] == 1


def test_a_quota_limit_does_not_use_the_network_schedule():
    got = S.decide(story(author_result="limit", author_ended=1000), ctx(now=1))
    assert got[0]["step"] == "resume" and "kind" not in got[0]


def test_network_retry_keeps_the_accounts_continue_window():
    assert S.decide(net(), ctx(cap=S.capacity(snapshot(a=5), {}), now=99999)) == []


def test_resume_launches_nothing_while_the_author_is_live():
    assert S.decide(net(running=["author"]), ctx(now=99999)) == []


def reconciling(runner_tuple, rc, step="submit"):
    cfg, write, launched, _, state = runner_tuple
    cfg["steps"][step] = "echo"
    cfg["reconcile"] = {step: f"exit {rc}"}
    (state / "runs").mkdir(parents=True, exist_ok=True)
    (state / "runs" / f"S1.{step}.json").write_text(
        json.dumps(
            {
                "story": "S1",
                "step": step,
                "pid": None,
                "started": 1,
                "head": "h1",
                "state": "launching",
            }
        )
    )
    write(
        facts(
            stories=[story(head="h1", submitted_sha="h0")]
            if step == "submit"
            else [story(verdicts={"codex": "approve"})]
        )
    )
    return cfg, launched, state


@pytest.mark.parametrize("step", ["submit", "finish"])
def test_a_step_whose_effect_already_happened_is_recorded_not_repeated(runner, step, monkeypatch):
    monkeypatch.setattr(S, "find_running", lambda ex: None)
    cfg, launched, state = reconciling(runner, 0, step)
    status = S.tick(cfg)
    assert (state / "runs" / f"S1.{step}.exit").read_text().strip() == "0"
    assert any("not repeated" in n for n in status["notes"])
    assert not [
        a for a in launched if a.get("step") == step and a.get("story") == "S1" and "need" in a
    ]


def test_an_unproven_effect_stays_a_failure_for_the_operator(runner, monkeypatch):
    monkeypatch.setattr(S, "find_running", lambda ex: None)
    cfg, launched, state = reconciling(runner, 1, "submit")
    status = S.tick(cfg)
    assert not (state / "runs" / "S1.submit.exit").exists()
    assert [a for a in launched if a["story"] == "S1"] == []
    assert "submit failed" in status["needs"][0]["need"]


# the auto-planner (operator, 24 Oct 2026) -----------------------------------------------------


def need(story="S1", why="finish failed on this commit: read its finish log"):
    return {"need": why, "story": story}


def test_a_planner_goes_to_a_need_only_on_its_second_tick_and_only_once():
    act, book = S.planner_act([need()], {}, ctx())
    assert act is None
    act, book = S.planner_act([need()], book, ctx())
    assert act["step"] == "planner" and act["story"] == S.PLANNER and act["needs"] == [need()]
    assert act["account"] in ("A", "B")
    again, book = S.planner_act([need()], book, ctx())
    assert again is None  # taken: never a second planner for the same need


def test_a_closed_need_that_recurs_gets_a_new_planner():
    _, book = S.planner_act([need()], {}, ctx())
    _, book = S.planner_act([need()], book, ctx())
    _, book = S.planner_act([], book, ctx())
    assert book == {"open": {}, "done": []}
    _, book = S.planner_act([need()], book, ctx())
    act, _ = S.planner_act([need()], book, ctx())
    assert act is not None


def test_no_planner_for_a_blocked_question_while_one_runs_or_with_no_opus_room():
    blocked = need(why="blocked: answer the question in its task packet (notes.blocked)")
    _, book = S.planner_act([blocked], {}, ctx())
    assert S.planner_act([blocked], book, ctx())[0] is None
    _, book = S.planner_act([need()], {}, ctx())
    running = ctx(busy={S.PLANNER: {("planner", "")}})
    act, kept = S.planner_act([need()], book, running)
    assert act is None and kept["done"] == []  # still due: the next free tick sends one
    full = ctx(cap=S.capacity(snapshot(a=5, b=5), {}))
    assert S.planner_act([need()], book, full)[0] is None


def test_tick_launches_the_planner_once_with_its_needs(runner):
    cfg, write, launched, _, _ = runner
    cfg["steps"]["planner"] = "agents/scripts/planner.sh {account} {needs}"
    write(facts(next=[], needs_preflight=["N1"]))
    S.tick(cfg)
    assert launched == []
    S.tick(cfg)
    S.tick(cfg)
    assert [a["step"] for a in launched] == ["planner"]
    assert launched[0]["needs"][0]["story"] == "N1"


def test_without_a_planner_step_no_planner_runs(runner):
    cfg, write, launched, _, _ = runner
    write(facts(next=[], needs_preflight=["N1"]))
    S.tick(cfg)
    S.tick(cfg)
    assert launched == []


def test_launch_writes_the_needs_file_the_command_names(tmp_path):
    (tmp_path / "runs").mkdir()
    cfg = {"project": {"runner": str(tmp_path)}, "steps": {"planner": "cp {needs} got.json"}}
    act = {"step": "planner", "story": S.PLANNER, "account": "A", "needs": [need()]}
    S.launch(cfg, tmp_path, act, None)
    ex = tmp_path / "runs" / f"{S.PLANNER}.planner.exit"
    for _ in range(100):
        if ex.exists() and ex.read_text().strip():
            break
        time.sleep(0.05)
    assert json.loads((tmp_path / "got.json").read_text()) == [need()]
