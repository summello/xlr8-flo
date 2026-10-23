"""`flo with-lock` and the launch, submit and finish scripts that take it (E23-S15).

The lock is a kernel flock, so a holder that is SIGKILLed frees it, and a bounded waiter exits 75.
Scripts run from a copy under tmp_path (R derives from the script's location) with a stub `flo`
that forwards `with-lock` to the real one and records the other calls.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.gates.flo_loader import SCRIPTS

STUB = """#!/usr/bin/env python3
import os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
if sys.argv[1] == "with-lock":
    os.execv(sys.executable, [sys.executable, os.path.join(here, "flo.real")] + sys.argv[1:])
log = os.path.join(here, "..", "..", "calls.log")
with open(log, "a") as f:
    f.write(f"{sys.argv[1]} start {time.time()}\\n")
time.sleep(1.0 if sys.argv[1] == "check" else 0.5)
with open(log, "a") as f:
    f.write(f"{sys.argv[1]} end {time.time()}\\n")
"""


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "root"
    sc = r / "agents" / "scripts"
    sc.mkdir(parents=True)
    for name in ("author.sh", "submit.sh", "finish.sh", "env.sh"):
        shutil.copy(SCRIPTS / name, sc / name)
    shutil.copy(SCRIPTS / "flo", sc / "flo.real")
    (sc / "flo").write_text(STUB)
    (sc / "flo").chmod(0o755)
    (r / ".scratch").mkdir()
    return r


def flo(root, *args, **kw):
    return subprocess.Popen(
        [sys.executable, str(root / "agents/scripts/flo.real"), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw,
    )  # fmt: skip


def hold(root, name="test"):
    p = flo(root, "with-lock", name, "--wait", "0", "--", "sleep", "30")
    lock = root / ".scratch" / f"{name}.lock"
    for _ in range(100):
        if lock.exists() and lock.read_text().strip():
            return p
        time.sleep(0.05)
    raise AssertionError("holder never took the lock")


def test_contender_exits_75_naming_lock_and_holder(root):
    holder = hold(root)
    try:
        c = flo(root, "with-lock", "test", "--wait", "0", "--", "true")
        _, err = c.communicate(timeout=20)
        assert c.returncode == 75
        assert "test.lock" in err and str(holder.pid) in err
    finally:
        holder.kill()


def test_sigkilled_holder_leaves_a_lock_the_next_acquirer_takes(root):
    holder = hold(root)
    holder.send_signal(signal.SIGKILL)
    holder.wait()
    assert (root / ".scratch" / "test.lock").exists()  # the file stays; the kernel freed the lock
    c = flo(root, "with-lock", "test", "--wait", "0", "--", "echo", "got it")
    out, _ = c.communicate(timeout=20)
    assert c.returncode == 0 and "got it" in out


def test_bounded_wait_gives_up_with_75(root):
    holder = hold(root)
    try:
        t = time.monotonic()
        c = flo(root, "with-lock", "test", "--wait", "1", "--", "true")
        c.communicate(timeout=20)
        assert c.returncode == 75
        assert time.monotonic() - t >= 1
    finally:
        holder.kill()


def test_child_status_and_arguments_are_preserved(root):
    c = flo(root, "with-lock", "x", "--", "sh", "-c", 'echo "$1"; exit 7', "sh", "a  b")
    out, _ = c.communicate(timeout=20)
    assert c.returncode == 7 and out == "a  b\n"
    c = flo(root, "with-lock", "x", "--", "sh", "-c", "kill -TERM $$")
    c.communicate(timeout=20)
    assert c.returncode == -signal.SIGTERM


def test_bad_lock_name_is_refused(root):
    c = flo(root, "with-lock", "../x", "--", "true")
    c.communicate(timeout=20)
    assert c.returncode != 0 and not (root / "x.lock").exists()


def story(root, sid, author="codex"):
    wt = root.parent / f"{root.name}-{sid}"
    wt.mkdir()
    for args in (("init", "-q", "-b", "M"), ("commit", "-q", "--allow-empty", "-m", "x")):
        subprocess.run(
            ["git", "-C", str(wt), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True
        )
    (wt / "task.json").write_text(json.dumps({"author": author, "milestone_branch": "M"}))
    venv = wt / "apps/api/.venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(sys.executable)
    return wt


def sh(root, script, *args, env=None):
    e = {**os.environ, **(env or {})}
    return subprocess.Popen(
        ["sh", str(root / "agents/scripts" / script), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e,
    )  # fmt: skip


def calls(root):
    out: dict[str, list[tuple[str, float]]] = {}
    for line in (root / "calls.log").read_text().splitlines():
        name, phase, ts = line.split()
        out.setdefault(name, []).append((phase, float(ts)))
    return out


def test_two_submits_of_one_story_second_exits_75_before_checking(root):
    story(root, "E00-S01")
    a = sh(root, "submit.sh", "E00-S01")
    time.sleep(0.5)  # a is inside its check
    b = sh(root, "submit.sh", "E00-S01")
    _, err = b.communicate(timeout=60)
    a.communicate(timeout=60)
    assert b.returncode == 75 and "submit.E00-S01.lock" in err
    assert a.returncode == 0
    assert len(calls(root)["check"]) == 2  # one start, one end: only a reached the check


def test_two_stories_check_together_but_submit_one_at_a_time(root):
    story(root, "E00-S01")
    story(root, "E00-S02")
    ps = [sh(root, "submit.sh", s) for s in ("E00-S01", "E00-S02")]
    for p in ps:
        p.communicate(timeout=60)
        assert p.returncode == 0
    rows = [(n, ph, ts) for n, v in calls(root).items() for ph, ts in v]
    checks = sorted((ts, ph) for n, ph, ts in rows if n == "check")
    assert [ph for _, ph in checks] == ["start", "start", "end", "end"]  # overlapped
    subs = sorted((ts, ph) for n, ph, ts in rows if n == "submit")
    assert [ph for _, ph in subs] == ["start", "end", "start", "end"]  # serialised


def test_finish_and_submit_scripts_have_no_mkdir_polling():
    for name in ("finish.sh", "submit.sh", "author.sh"):
        text = (SCRIPTS / name).read_text()
        assert "until mkdir" not in text and "finish.lock" not in text, name
        assert "with-lock" in text, name


def pidfile_author(root, sid, pid):
    pf = root / ".scratch" / f"{sid}.author.pid"
    pf.write_text(f"{pid}\n")
    return pf


def test_author_refuses_when_the_pid_file_names_a_live_process(root):
    story(root, "E00-S01", author="nobody")
    live = subprocess.Popen(["sleep", "30"])
    try:
        pf = pidfile_author(root, "E00-S01", live.pid)
        p = sh(root, "author.sh", "E00-S01")
        _, err = p.communicate(timeout=30)
        assert p.returncode == 75
        assert f"author of E00-S01 already running (pid {live.pid})" in err
        assert pf.read_text() == f"{live.pid}\n"
        assert not (root / ".scratch" / "E00-S01.prompt").exists()  # launched nothing
        live.kill()
        live.wait()
        p = sh(root, "author.sh", "E00-S01")
        _, err = p.communicate(timeout=30)
        assert (root / ".scratch" / "E00-S01.prompt").exists()  # got past the check
        assert "no launcher" in err
    finally:
        live.kill()


@pytest.mark.parametrize("text", ["", "junk\n", "0\n", "-1\n"])
def test_author_ignores_a_malformed_pid_file(root, text):
    story(root, "E00-S01", author="nobody")
    (root / ".scratch" / "E00-S01.author.pid").write_text(text)
    p = sh(root, "author.sh", "E00-S01")
    _, err = p.communicate(timeout=30)
    assert p.returncode == 1 and "no launcher" in err


def test_simultaneous_launches_start_one_author(root, tmp_path):
    story(root, "E00-S01", author="codex")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "codex").write_text("#!/bin/sh\necho x >> '%s'\nsleep 5\n" % (tmp_path / "launches"))
    (bin_ / "codex").chmod(0o755)
    env = {"PATH": f"{bin_}:{os.environ['PATH']}"}
    gate = hold(root, "launch.E00-S01")  # both launchers queue behind it, past any unlocked check
    ps = [sh(root, "author.sh", "E00-S01", env=env) for _ in range(2)]
    time.sleep(1.5)
    gate.kill()
    codes = sorted(p.wait(timeout=60) for p in ps)
    time.sleep(1)
    try:
        assert codes == [0, 75]
        assert (tmp_path / "launches").read_text().count("x") == 1
    finally:
        pid = (root / ".scratch" / "E00-S01.author.pid").read_text().strip()
        subprocess.run(["pkill", "-P", pid])
        subprocess.run(["kill", pid])
