"""`flo reassign` moves a story only when nothing can be running it (E23-S06).

Moving a story out from under a live worker puts two authors on one branch, and moving it to an
agent `flo assign` would refuse breaks the routing rules. A gated story must keep its Opus final
review whoever authors it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.gates.flo_loader import SCRIPTS, load_flo
from tests.gates.test_flo_assign import fleet

F = load_flo()
SID = "E00-S01"
API = Path(__file__).resolve().parents[2]


def git_in(wt, *args):
    subprocess.run(
        ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


def dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def make_world(tmp_path, kind="ui", tags=(), author="opencode-nemotron", config=None):
    """A runner root, a story worktree that is a real git repo, and the stubs flo reads through."""
    root, wt = tmp_path / "root", tmp_path / "wt"
    (root / ".scratch").mkdir(parents=True)
    wt.mkdir()
    git_in(wt, "init", "-q", "-b", "milestone/M9")
    (wt / ".gitignore").write_text("task.json\nreviews/\n")
    (wt / "README").write_text("x\n")
    git_in(wt, "add", ".")
    git_in(wt, "commit", "-q", "-m", "base")
    git_in(wt, "checkout", "-q", "-b", "story/x")
    packet = {
        "author": author,
        "reviewers": ["codex"],
        "opus_final": False,
        "milestone_branch": "milestone/M9",
        "sessions": {author: {"agent": author, "id": "s-1", "source": "supplied"}},
        "substitutions": [],
    }
    (wt / "task.json").write_text(json.dumps(packet))
    story = {
        "id": SID, "t": "T", "k": kind, "s": "S", "tags": list(tags),
        "reqs": ["SUP-005"], "dep": [], "st": "in_progress",
    }  # fmt: skip
    roadmap = {
        "milestones": [{"id": "M9", "name": "x", "branch": "milestone/M9"}],
        "epics": [{"id": "E00", "milestone": "M9", "stories": [story]}],
    }
    return root, wt, roadmap, config or fleet()


@pytest.fixture
def world(tmp_path, monkeypatch):
    def build(**kwargs):
        root, wt, roadmap, config = make_world(tmp_path, **kwargs)
        monkeypatch.setattr(F, "ROOT", root)
        monkeypatch.setattr(F, "worktree_path", lambda sid: wt)
        monkeypatch.setattr(F, "roadmap", lambda: roadmap)
        monkeypatch.setattr(F, "load_yaml", lambda path: config)
        monkeypatch.setattr(F, "state", lambda: {"spend": {}})
        monkeypatch.setattr(F, "free_accounts", lambda name, cfg: ["A"])
        return root, wt

    return build


def packet(wt):
    return json.loads((wt / "task.json").read_text())


def test_a_live_worker_blocks_the_reassignment(world, capsys):
    root, wt = world()
    (root / ".scratch" / f"{SID}.author.pid").write_text(f"{os.getpid()}\n")
    before = (wt / "task.json").read_text()
    with pytest.raises(SystemExit):
        F.cmd_reassign([SID])
    assert "still running" in capsys.readouterr().err
    assert (wt / "task.json").read_text() == before


def test_a_dead_worker_and_a_clean_tree_is_reassigned(world):
    root, wt = world()
    (root / ".scratch" / f"{SID}.author.pid").write_text(f"{dead_pid()}\n")
    F.cmd_reassign([SID, "--reason", "nemotron hit a limit"])
    pkt = packet(wt)
    assert pkt["author"] == "sonnet"  # never handed back to the agent it came from
    assert pkt["substitutions"] == [
        {"from": "opencode-nemotron", "to": "sonnet", "reason": "nemotron hit a limit"}
    ]
    assert pkt["sessions"]["opencode-nemotron"]["id"] == "s-1"  # the old session is kept


def test_an_untracked_or_staged_change_blocks_it(world):
    _, wt = world()
    (wt / "scratch.txt").write_text("work\n")
    with pytest.raises(SystemExit):
        F.cmd_reassign([SID])
    git_in(wt, "add", "scratch.txt")
    with pytest.raises(SystemExit):
        F.cmd_reassign([SID])
    assert packet(wt)["author"] == "opencode-nemotron"


def test_a_commit_of_the_authors_own_blocks_it(world, capsys):
    _, wt = world()
    (wt / "README").write_text("changed\n")
    git_in(wt, "commit", "-q", "-am", "work")
    with pytest.raises(SystemExit):
        F.cmd_reassign([SID])
    assert "1 commit(s) past" in capsys.readouterr().err
    assert packet(wt)["author"] == "opencode-nemotron"


def test_a_gated_story_keeps_its_opus_final_and_an_independent_reviewer(world):
    _, wt = world(kind="ledger", tags=["money"], author="sonnet")
    F.cmd_reassign([SID])
    pkt = packet(wt)
    assert pkt["author"] == "codex"
    assert pkt["opus_final"] is True
    assert pkt["reviewers"] == ["qwen"]
    assert pkt["author"] not in pkt["reviewers"]


def test_nobody_else_eligible_leaves_the_story_where_it_is(world):
    config = fleet()
    config["agents"]["codex"]["allowed_kinds"] = ["crud"]
    _, wt = world(kind="engine", author="sonnet", config=config)
    before = (wt / "task.json").read_text()
    with pytest.raises(SystemExit):
        F.cmd_reassign([SID])
    assert (wt / "task.json").read_text() == before


def test_a_sole_author_override_cannot_be_reassigned(world):
    config = fleet()
    config["milestone_overrides"] = {
        "M9": {"sole_author": "codex", "reviewers": ["opus"], "opus_final_all": True}
    }
    _, wt = world(kind="engine", author="codex", config=config)
    with pytest.raises(SystemExit):
        F.cmd_reassign([SID])
    assert packet(wt)["author"] == "codex"


def test_a_reassign_that_waited_for_the_lock_refuses_to_move_it_again(world, capsys):
    _, wt = world()
    pkt = packet(wt)
    pkt["author"] = "sonnet"  # someone else reassigned it after this caller read the author
    (wt / "task.json").write_text(json.dumps(pkt))
    epic, st = F.find(F.roadmap(), SID)
    with pytest.raises(SystemExit):
        F._reassign_locked(SID, epic, st, "opencode-nemotron", "late")
    assert "already reassigned" in capsys.readouterr().err
    assert packet(wt)["author"] == "sonnet"


def test_a_held_lock_refuses_and_the_lock_is_released_afterwards(world):
    root, wt = world()
    lock = root / ".scratch" / f"{SID}.lock"
    lock.mkdir()
    with pytest.raises(SystemExit):
        F.cmd_reassign([SID])
    assert lock.exists()  # someone else's lock is left alone
    lock.rmdir()
    F.cmd_reassign([SID])
    assert not lock.exists()
    (root / ".scratch" / f"{SID}.author.pid").write_text(f"{os.getpid()}\n")
    with pytest.raises(SystemExit):
        F.cmd_reassign([SID])  # fails inside the lock
    assert not lock.exists()


DRIVER = """
import json, sys, time
from pathlib import Path
from tests.gates.flo_loader import load_flo
F = load_flo()
cfg = json.loads(Path(sys.argv[1]).read_text())
F.ROOT = Path(cfg["root"])
F.worktree_path = lambda sid: Path(cfg["wt"])
F.roadmap = lambda: cfg["roadmap"]
F.load_yaml = lambda path: cfg["fleet"]
F.state = lambda: {"spend": {}}
F.free_accounts = lambda name, c: ["A"]
real_alive = F._alive
def slow_alive(pidfile):
    time.sleep(0.5)  # widen the window between the checks and the write
    return real_alive(pidfile)
F._alive = slow_alive
while not Path(cfg["go"]).exists():
    time.sleep(0.01)
F.cmd_reassign([cfg["sid"]])
"""


def test_two_simultaneous_reassigns_exactly_one_succeeds(tmp_path):
    root, wt, roadmap, config = make_world(tmp_path)
    go = tmp_path / "go"
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "root": str(root), "wt": str(wt), "roadmap": roadmap, "fleet": config,
        "go": str(go), "sid": SID,
    }))  # fmt: skip
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", DRIVER, str(settings)],
            cwd=API, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(2)
    ]  # fmt: skip
    time.sleep(1.0)  # both are polling for the start signal
    go.write_text("go")
    results = [(p.wait(timeout=60), p.stderr.read()) for p in procs]
    codes = sorted(code for code, _ in results)
    assert codes[0] == 0 and codes[1] != 0, results
    pkt = packet(wt)
    assert pkt["author"] == "sonnet"
    assert len(pkt["substitutions"]) == 1  # moved once, not twice
    assert not (root / ".scratch" / f"{SID}.lock").exists()


def test_author_launcher_takes_the_same_lock_with_a_bounded_wait():
    text = (SCRIPTS / "author.sh").read_text()
    assert 'LOCK="$D/$S.lock"' in text
    assert 'until mkdir "$LOCK"' in text
    assert '[ "$N" -gt 30 ]' in text  # a stuck lock cannot hang a launch
    assert text.index('until mkdir "$LOCK"') < text.index(
        'A="$(python3'
    )  # before task.json is read
