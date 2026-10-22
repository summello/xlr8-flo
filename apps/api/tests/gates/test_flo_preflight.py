"""`flo start` refuses a story whose packet has no passing pre-flight record (E23-S05).

The record is a hash of the packet as it stood when the pre-flight passed, so an amendment after
the pre-flight invalidates it. It proves a pass was filed for this text, not that the pre-flight
was good.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.gates.flo_loader import load_flo

F = load_flo()
SID = "E00-S01"
REPO = Path(__file__).resolve().parents[4]


class Reached(Exception):
    """cmd_start got past the pre-flight check and tried to touch git."""


@pytest.fixture
def runner(tmp_path, monkeypatch):
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / f"{SID}.md").write_text("packet v1\n")
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

    def git(*args, **kwargs):
        raise Reached(args)

    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.setattr(F, "roadmap", lambda: roadmap)
    monkeypatch.setattr(
        F,
        "load_yaml",
        lambda path: {"agents": {"qwen": {}}, "policy": {"max_stories_in_flight": 6}},
    )
    monkeypatch.setattr(F, "git", git)
    return tmp_path


def file_preflight(verdict="pass", reviewer="qwen"):
    F.cmd_preflight([SID, "--reviewer", reviewer, "--verdict", verdict])


def test_start_is_refused_without_a_record(runner, capsys):
    with pytest.raises(SystemExit) as exc:
        F.cmd_start([SID])
    assert exc.value.code != 0
    assert "no pre-flight record" in capsys.readouterr().err


def test_start_proceeds_with_a_matching_pass(runner):
    file_preflight()
    with pytest.raises(Reached):  # past the check, into the first git call
        F.cmd_start([SID])


def test_an_edit_to_the_packet_after_pre_flight_refuses_the_start(runner, capsys):
    file_preflight()
    with (runner / "design" / f"{SID}.md").open("a") as fh:
        fh.write("an amendment\n")
    with pytest.raises(SystemExit):
        F.cmd_start([SID])
    assert "packet changed since pre-flight" in capsys.readouterr().err


def test_a_failed_pre_flight_does_not_unlock_the_start(runner, capsys):
    file_preflight(verdict="fail")
    with pytest.raises(SystemExit):
        F.cmd_start([SID])
    assert "not 'pass'" in capsys.readouterr().err


def test_a_record_for_another_story_is_refused(runner):
    file_preflight()
    other = "E00-S02"
    (runner / "design" / f"{other}.md").write_text("packet v1\n")
    src = runner / ".scratch" / f"{SID}.preflight.json"
    (runner / ".scratch" / f"{other}.preflight.json").write_text(src.read_text())
    assert F.preflight_state(other)[0] is None
    assert "another story" in F.preflight_state(other)[1]


def test_an_unknown_reviewer_writes_nothing(runner):
    with pytest.raises(SystemExit):
        F.cmd_preflight([SID, "--reviewer", "nobody", "--verdict", "pass"])
    assert not (runner / ".scratch" / f"{SID}.preflight.json").exists()


def test_a_bad_verdict_or_missing_reviewer_is_a_usage_error(runner):
    with pytest.raises(SystemExit):
        F.cmd_preflight([SID, "--reviewer", "qwen", "--verdict", "maybe"])
    with pytest.raises(SystemExit):
        F.cmd_preflight([SID, "--verdict", "pass"])


def test_facts_offers_only_stories_with_a_valid_record(runner, capsys):
    F.cmd_facts([])
    facts = json.loads(capsys.readouterr().out)
    assert facts["next"] == []
    assert facts["needs_preflight"] == [SID]
    file_preflight()
    capsys.readouterr()  # drop the confirmation line so only the JSON is parsed
    F.cmd_facts([])
    facts = json.loads(capsys.readouterr().out)
    assert facts["next"] == [SID]
    assert facts["needs_preflight"] == []


def test_scratch_is_ignored_by_git():
    assert ".scratch/" in (REPO / ".gitignore").read_text().splitlines()
