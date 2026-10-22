"""The launchers take their model from agents.yaml and record it (E23-S03).

A launcher that hardcodes a model makes a config edit change nothing, and a silent swap
corrupts the scorecard rows that routing is decided from.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.gates.flo_loader import load_flo

F = load_flo()
SCRIPTS = Path(__file__).resolve().parents[4] / "agents" / "scripts"
MODEL_LITERAL = re.compile(r"nemotron-3-ultra|qwen3|kimi-k2|deepseek-v4")


@pytest.fixture
def story(tmp_path, monkeypatch):
    """A fixture fleet config and a story worktree holding a task.json."""
    config: dict = {}

    def write(agents):
        config["agents"] = agents

    write({"opencode-nemotron": {"model": "vendor/model-a:free"}, "qwen": {"model": "q/model-b"}})
    wt = tmp_path / "wt"
    wt.mkdir()
    packet = {
        "author": "opencode-nemotron",
        "sessions": {
            "opencode-nemotron": {
                "agent": "opencode-nemotron",
                "id": "s-1",
                "source": "supplied",
                "account": "A",
            }
        },
    }
    (wt / "task.json").write_text(json.dumps(packet))
    monkeypatch.setattr(F, "load_yaml", lambda path: config)
    monkeypatch.setattr(F, "worktree_path", lambda sid: wt if sid == "E00-S01" else tmp_path / sid)
    return write, wt


def record(wt):
    return json.loads((wt / "task.json").read_text())["sessions"]


@pytest.mark.parametrize("script", ["author.sh", "review.sh"])
def test_launchers_name_no_model_literal(script):
    text = (SCRIPTS / script).read_text()
    assert not MODEL_LITERAL.search(text)


def test_the_literal_check_catches_a_hardcoded_model():
    planted = "opencode run -m openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"
    assert MODEL_LITERAL.search(planted)


def test_author_launcher_asks_flo_before_it_launches():
    text = (SCRIPTS / "author.sh").read_text()
    assert text.index("launch-model") < text.index("opencode run")


def test_prints_and_records_the_configured_model(story, capsys):
    _, wt = story
    F.cmd_launch_model(["E00-S01"])
    assert capsys.readouterr().out.strip() == "vendor/model-a:free"
    rec = record(wt)["opencode-nemotron"]
    assert rec["model"] == "vendor/model-a:free"
    assert (rec["id"], rec["account"], rec["source"]) == ("s-1", "A", "supplied")


def test_a_config_change_changes_the_launch_model(story, capsys):
    write, _ = story
    write({"opencode-nemotron": {"model": "vendor/model-c:free"}})
    F.cmd_launch_model(["E00-S01"])
    assert capsys.readouterr().out.strip() == "vendor/model-c:free"


def test_an_agent_without_a_model_fails_and_prints_nothing(story, capsys):
    write, wt = story
    write({"opencode-nemotron": {"cli": "opencode run"}})
    before = (wt / "task.json").read_text()
    with pytest.raises(SystemExit) as exc:
        F.cmd_launch_model(["E00-S01"])
    assert exc.value.code != 0
    out = capsys.readouterr()
    assert out.out == ""
    assert "opencode-nemotron" in out.err
    assert (wt / "task.json").read_text() == before


def test_reviewer_model_is_recorded_under_the_reviewer(story, capsys):
    _, wt = story
    F.cmd_launch_model(["E00-S01", "--reviewer", "qwen"])
    assert capsys.readouterr().out.strip() == "q/model-b"
    sessions = record(wt)
    assert sessions["qwen"]["model"] == "q/model-b"
    assert "model" not in sessions["opencode-nemotron"]


def test_release_review_prints_the_model_without_a_packet(story, capsys):
    F.cmd_launch_model(["M9", "--reviewer", "qwen"])
    assert capsys.readouterr().out.strip() == "q/model-b"


def test_unknown_agent_is_refused(story):
    with pytest.raises(SystemExit):
        F.cmd_launch_model(["M9", "--reviewer", "nobody"])


def test_real_config_names_a_model_for_every_launched_agent():
    # PyYAML is not a dependency of the API test environment, so read the file as text
    text = (SCRIPTS.parents[1] / "agents" / "agents.yaml").read_text()
    for name in ("opencode-nemotron", "qwen", "kimi", "deepseek"):
        block = re.search(rf"^  {name}:\n((?:    .*\n|\n|  #.*\n)*)", text, re.M)
        assert block and re.search(r"^    model: \S+", block.group(1), re.M), name


def sonnet_story(write, wt, **session):
    """The story's author is Sonnet, with a session record flo account wrote earlier."""
    write({"sonnet": {"model": "sonnet", "reasoning": "medium"}})
    record = {"agent": "sonnet", "id": "s-9", "source": "launch:--session-id", "account": "B"}
    record.update(session)
    packet = {"author": "sonnet", "sessions": {"sonnet": record}}
    (wt / "task.json").write_text(json.dumps(packet))


def test_effort_is_printed_and_recorded_without_touching_the_session(story, capsys):
    write, wt = story
    sonnet_story(write, wt, model="sonnet")
    F.cmd_launch_model(["E00-S01", "--effort"])
    assert capsys.readouterr().out.strip() == "medium"
    rec = record(wt)["sonnet"]
    assert rec["effort"] == "medium"
    assert (rec["id"], rec["account"], rec["source"], rec["model"]) == (
        "s-9",
        "B",
        "launch:--session-id",
        "sonnet",
    )


def test_a_config_change_changes_the_printed_effort(story, capsys):
    write, wt = story
    sonnet_story(write, wt)
    write({"sonnet": {"model": "sonnet", "reasoning": "high"}})
    F.cmd_launch_model(["E00-S01", "--effort"])
    assert capsys.readouterr().out.strip() == "high"


def test_an_agent_without_reasoning_fails_and_prints_nothing(story, capsys):
    write, wt = story
    sonnet_story(write, wt)
    write({"sonnet": {"model": "sonnet"}})
    before = (wt / "task.json").read_text()
    with pytest.raises(SystemExit) as exc:
        F.cmd_launch_model(["E00-S01", "--effort"])
    assert exc.value.code != 0
    out = capsys.readouterr()
    assert out.out == "" and "sonnet" in out.err and "reasoning" in out.err
    assert (wt / "task.json").read_text() == before


def test_the_model_flag_still_prints_the_model_for_the_same_agent(story, capsys):
    write, wt = story
    sonnet_story(write, wt)
    F.cmd_launch_model(["E00-S01"])
    assert capsys.readouterr().out.strip() == "sonnet"
    assert "effort" not in record(wt)["sonnet"]


def test_author_launcher_names_no_sonnet_model_or_effort_and_asks_flo():
    text = (SCRIPTS / "author.sh").read_text()
    assert "--effort medium" not in text and "--model sonnet" not in text
    assert text.count("launch-model") >= 3  # nemotron's model, then sonnet's model and effort
    assert "launch-model" in text and '"$S" --effort' in text
    assert text.index('"$S" --effort') < text.index("claude -p --model")


def test_the_committed_config_says_sonnet_runs_at_medium():
    text = (SCRIPTS.parents[1] / "agents" / "agents.yaml").read_text()
    block = re.search(r"^  sonnet:\n((?:    .*\n|\n|  #.*\n)*)", text, re.M)
    assert block, "sonnet block not found"
    assert re.search(r"^    reasoning: medium\b", block.group(1), re.M)
