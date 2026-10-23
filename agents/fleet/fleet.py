#!/usr/bin/env python3
"""fleet: a user-scoped supervisor for an AI delivery fleet. Project-agnostic, stdlib only.

One `tick` reads a project's facts, decides the next step for every in-flight story, launches each
step detached and exits. launchd runs a tick every few minutes, so nothing waits in a loop, nothing
can be orphaned, and a capped or closed Claude session no longer stops the work.

    fleet tick      --config <project>/fleet.toml [--dry-run]
    fleet status    [--config <project>/fleet.toml]
    fleet pause     --config ...      (the operator's switch: a paused fleet only watches)
    fleet resume    --config ...
    fleet install   --config ... [--active]     launchd agent, every [project].interval seconds
    fleet uninstall --config ...

The project supplies (fleet.toml):
    [project] name, runner (the checkout the fleet runs from), facts (a command printing JSON facts)
    [steps]   shell commands per step: begin submit review resume round fix finish ci_watch
              placeholders {story} {account} {reviewer} {runner} and any key of [vars]
    [limits]  command printing the limits snapshot, and the thresholds that gate work
    [backup]  paths under the runner copied once a day to dest

Facts JSON (the contract; see README.md): max_in_flight, max_rounds, in_flight, line_stopped[],
next[], free_accounts[], stories[] with id, author, account, gated, blocked, merged, rounds,
running[], author_result, head, submitted_sha, verdicts{reviewer: approve|changes|stale|missing|
malformed}, ci_red, ci.

The fleet never chooses a model, never merges to a protected branch, never answers a blocked
question: it starts the project's own steps in the project's own order, within the limits, and
tells the operator what only they can decide.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import tomllib

HOME = Path.home()
STATE = HOME / ".claude" / "fleet" / "state"
BACKOFF = (60, 300, 900)                # seconds before network retry 1, 2, 3 (E23-S17)
HEAVY = {"submit", "finish", "begin"}   # steps that run the light check or installs: bounded together


# ── pure rules ───────────────────────────────────────────────────────────────

def capacity(snapshot: dict | None, lim: dict) -> dict:
    """Which accounts may start new work or continue existing work, and whether Codex may review.

    Unknown limits (no snapshot) allow work: a missing monitor must not stall the fleet, and the
    agents' own limit errors are still caught (author_result "limit" waits for the reset)."""
    start5, startw = lim.get("claude_start", [25, 15])
    cont5, contw = lim.get("claude_continue", [10, 5])
    codex5, codexw = lim.get("codex", [20, 10])
    out = {"known": snapshot is not None, "start": {}, "continue": {}, "codex": True, "left": {}}
    if snapshot is None:
        if lim.get("disabled_accounts"):
            snapshot = {}
        else:
            out["start"] = out["continue"] = None
            return out
    for a in lim.get("disabled_accounts", []):   # the operator's switch: no work on this account
        out["start"][a] = out["continue"][a] = False
    for row in (snapshot.get("limits") or {}).get("claude") or []:
        if row["account"] in lim.get("disabled_accounts", []):
            continue
        if row.get("stale"):        # unknown, not 100%: the collector could not read it lately
            continue
        a, f, w = row["account"], row["five"]["leftPct"], row["week"]["leftPct"]
        out["left"][a] = (f, w)
        out["start"][a] = f >= start5 and w >= startw
        out["continue"][a] = f >= cont5 and w >= contw
    cx = snapshot.get("codex")
    if cx:
        out["codex"] = cx["five"]["leftPct"] >= codex5 and cx["week"]["leftPct"] >= codexw
        out["left"]["codex"] = (cx["five"]["leftPct"], cx["week"]["leftPct"])
    return out


def can(cap: dict, mode: str, account: str | None) -> bool:
    table = cap[mode]
    return True if table is None or account is None else table.get(account, True)


def author_can(story: dict, cap: dict, mode: str = "continue") -> bool:
    """Claude authors are gated per account; Codex authors by Codex's windows; others are free."""
    if story["author"] == "codex":
        return cap["codex"]
    return can(cap, mode, story.get("account"))


def opus_account(cap: dict, accounts: list[str]) -> str | None:
    """A fresh Opus reviewer runs on whichever account has the most five-hour headroom."""
    ok = [a for a in accounts if can(cap, "continue", a)]
    if not ok:
        return None
    return max(ok, key=lambda a: cap["left"].get(a, (100, 100))[0])


def decide(s: dict, ctx: dict) -> list[dict]:
    """The next action for one story: [] waits, {"need": why} asks the operator, else a step.

    ctx: cap, busy (engine-launched steps still running, per story), results (last exit and head per
    story and step), line_stopped, max_rounds, accounts (Claude accounts for Opus reviews)."""
    sid, cap = s["id"], ctx["cap"]
    if s["running"] or ctx["busy"].get(sid):
        return []
    step = lambda name, **kw: [{"step": name, "story": sid, **kw}]
    need = lambda why: [{"need": why, "story": sid}]
    if s["merged"]:
        if not s["ci_red"]:
            return []
        fix, sub = ctx["results"].get((sid, "fix", "")), ctx["results"].get((sid, "submit", "")) or {}
        if fix and fix.get("exit") is not None and fix.get("started", 0) > sub.get("started", 0):
            # a merged story is never resubmitted, so its red stays red after the fix: resending
            # loops (E22-S12, 22 Oct, every tick)
            return need("a CI fix already ran on this merged story: move its commits onto the milestone, "
                        "or clear .scratch/<S>.ci-red if the milestone fixed it")
        return step("fix", account=s.get("account")) if author_can(s, cap) else []
    if s["blocked"]:
        return need("blocked: answer the question in its task packet (notes.blocked)")
    ar = s["author_result"]
    if ar in ("limit", "none"):     # stopped on a usage window, or never ran: resume when there is room
        return step("resume", account=s.get("account")) if author_can(s, cap) else []
    if ar == "network":             # a lost connection, not a window: bounded backoff, then the operator
        last = ctx["results"].get((sid, "resume", "")) or {}
        tries = last.get("attempt", 0) if last.get("kind") == "network" and last.get("head") == s["head"] else 0
        if tries >= len(BACKOFF):
            return need(f"the network failed {tries} retries in a row: check the connection, then resume")
        due = (s.get("author_ended") or 0) + BACKOFF[tries]
        if ctx.get("now", time.time()) < due or not author_can(s, cap):
            return []
        return step("resume", account=s.get("account"), kind="network", attempt=tries + 1, retry_at=due)
    if ar == "failed":
        return need("the author's last run failed: read its log, then resume or reassign")
    # red on the submitted commit, and the author has not committed since: fix. Commits since
    # (a round, or a fix) are submitted first, so CI judges the head rather than an old commit.
    if s["ci_red"] and s["head"] == s["submitted_sha"]:
        last = ctx["results"].get((sid, "fix", ""))
        if last and last.get("head") == s["head"] and last.get("exit") is not None:
            # the author ran on this red and committed nothing: resending loops (21 Oct, every tick)
            return need("a CI fix ran and committed nothing: read its log; the cause may be outside the story")
        return step("fix", account=s.get("account")) if author_can(s, cap) else []
    if s["head"] != s["submitted_sha"]:
        last = ctx["results"].get((sid, "submit", ""))
        if last and last.get("head") == s["head"] and last.get("exit") not in (0, None):
            return need("submit failed on this commit: read its submit log")
        return step("submit")
    verdicts = s["verdicts"]
    if "malformed" in verdicts.values():
        return need("a verdict file is malformed: fix the JSON or rerun that reviewer")
    todo = [r for r, v in verdicts.items() if v in ("missing", "stale")]
    if todo:
        acts = []
        for r in todo:
            if r == "codex" and cap["codex"]:
                acts += step("review", reviewer=r, account="")
            elif r == "opus":
                acct = opus_account(cap, ctx["accounts"])
                if acct:
                    acts += step("review", reviewer=r, account=acct)
            elif r not in ("codex", "opus"):
                acts += step("review", reviewer=r, account="")   # metered: the script keeps its own floor
        return acts
    if "changes" in verdicts.values():
        if s["rounds"] >= ctx["max_rounds"]:
            return need(f"{s['rounds']} review rounds without approval: arbitrate (flo escalate {sid})")
        return step("round", account=s.get("account")) if author_can(s, cap) else []
    if ctx["line_stopped"]:
        return []                   # a red milestone stops every merge
    if s["gated"] and s.get("ci") != "green":
        return []                   # gated stories merge only on green story CI; ci-watch marks red
    last = ctx["results"].get((sid, "finish", ""))
    if last and last.get("head") == s["head"] and last.get("exit") not in (0, None):
        return need("finish failed on this commit: read its finish log")
    return step("finish")


def plan(facts: dict, ctx: dict) -> list[dict]:
    """Every action this tick: per-story steps, then at most one new story if there is room."""
    acts = []
    for s in facts["stories"]:
        got = decide(s, ctx)
        # one reviewer at a time per story: both plant and revert violations in one worktree
        # (E23-S14). Codex first; the next reviewer starts on a later tick, once this one exits.
        revs = [a for a in got if a.get("step") == "review"]
        if len(revs) > 1:
            keep = min(revs, key=lambda a: a.get("reviewer") != "codex")
            got = [a for a in got if a.get("step") != "review"] + [keep]
        acts += got
    if not ctx.get("allow_start", True):        # the operator's switch: finish what is in flight, start nothing
        return acts
    if ctx["line_stopped"] or facts["in_flight"] >= facts["max_in_flight"]:
        return acts
    room = [a for a in facts.get("free_accounts") or [] if can(ctx["cap"], "start", a)]
    if facts.get("next") and (room or not facts.get("free_accounts")):
        acts.append({"step": "begin", "story": facts["next"][0], "account": room[0] if room else ""})
    elif facts.get("next"):
        # E23-S11, output only: say why a ready story did not start
        acts.append({"note": f"start blocked: no Claude account has room (next: {facts['next'][0]})"})
    elif facts.get("needs_preflight"):
        # E23-S11, output only: every ready story lacks a pre-flight record, so the fleet would idle silently
        first = facts["needs_preflight"][0]
        acts.append({"need": f"nothing can start: {first} and others have no pre-flight record; run flo preflight",
                     "story": first})
    return acts


def heavy_slots(acts: list[dict], busy_heavy: int, limit: int) -> list[dict]:
    """Drop heavy steps beyond the limit; they are decided again next tick."""
    out, n = [], busy_heavy
    for a in acts:
        if a.get("step") in HEAVY:
            if n >= limit:
                continue
            n += 1
        out.append(a)
    return out


# ── I/O ──────────────────────────────────────────────────────────────────────

def load(path: str) -> dict:
    p = Path(path).expanduser()
    cfg = tomllib.loads(p.read_text())
    cfg["_path"] = str(p)
    cfg["project"]["runner"] = str(Path(cfg["project"]["runner"]).expanduser())
    return cfg


def state_dir(cfg: dict) -> Path:
    d = STATE / cfg["project"]["name"]
    (d / "runs").mkdir(parents=True, exist_ok=True)
    return d


def sh(cmd: str, cwd: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", "-c", cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)


def fill(cmd: str, cfg: dict, **kw) -> str:
    vals = {**cfg.get("vars", {}), "runner": cfg["project"]["runner"], **kw}
    return cmd.format(**{k: shlex.quote(str(v)) if k in kw else v for k, v in vals.items()})


def put(path: Path, doc: dict) -> None:
    """Write JSON so a reader sees the old file or the new one, never half of one."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc))
    os.replace(tmp, path)


def lstart(pid: int) -> str | None:
    """The process start time as `ps` prints it, whitespace-normalised; None when no such process."""
    r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return " ".join(r.stdout.split()) or None if r.returncode == 0 else None


def alive(pid: int | None, start: str | None) -> bool:
    """Live means this pid exists AND started when the record says: macOS reuses pids."""
    return bool(pid and start) and lstart(pid) == start


def find_running(ex: Path) -> tuple[int, str] | None:
    """A run whose record never got its pid: find its shell by the exit file it will write."""
    r = subprocess.run(["ps", "-axo", "pid=,lstart=,command="], capture_output=True, text=True, check=False)
    tail = f"echo $? > {shlex.quote(str(ex))}"
    for line in r.stdout.splitlines():
        if line.rstrip().endswith(tail):
            f = line.split()
            return int(f[0]), " ".join(f[1:6])
    return None


def records(d: Path) -> tuple[dict, dict]:
    """Engine-launched steps: busy[story] = {(step, reviewer) still running},
    results[(story, step, reviewer)] = last record. Reviewer is "" for every step but review."""
    busy, results = {}, {}
    for f in (d / "runs").glob("*.json"):
        r = json.loads(f.read_text())
        legacy = r["step"] == "review" and "reviewer" not in r   # STORY.review.json, pre E23-S14
        r.setdefault("reviewer", "")
        ex = f.with_suffix(".exit")
        r["exit"] = int(ex.read_text().strip() or -1) if ex.exists() else None
        if r["exit"] is None and not r.get("pid") and (found := find_running(ex)):
            # launched, then the supervisor died before it wrote the pid: adopt the process
            r.update(pid=found[0], start=found[1], state="running")
            put(f, {k: v for k, v in r.items() if k != "exit"})
        if r["exit"] is None and alive(r.get("pid"), r.get("start")):
            if not legacy:
                busy.setdefault(r["story"], set()).add((r["step"], r["reviewer"]))
        elif r["exit"] is None:
            r["exit"] = -1          # died without recording an exit: a crash, a reboot, a reused pid, no identity
        results[(r["story"], r["step"], r["reviewer"])] = r
    return busy, results


def launch(cfg: dict, d: Path, act: dict, head: str | None) -> None:
    """Start one step detached; its exit code lands beside its record. The record is written first,
    so no process exists that the next tick cannot see."""
    reviewer = (act.get("reviewer") or "") if act["step"] == "review" else ""
    name = f"{act['story']}.{act['step']}" + (f".{reviewer}" if reviewer else "")
    cmd = fill(cfg["steps"][act["step"]], cfg, story=act["story"], account=act.get("account") or "",
               reviewer=reviewer)
    rec, out, ex = d / "runs" / f"{name}.json", d / "runs" / f"{name}.out", d / "runs" / f"{name}.exit"
    ex.unlink(missing_ok=True)
    with open(out, "a") as fh:
        fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {cmd}\n")
    doc = {"story": act["story"], "step": act["step"], "reviewer": reviewer, "state": "launching",
           "pid": None, "start": None, "started": time.time(), "head": head, "cmd": cmd,
           "attempt": act.get("attempt", 1), "kind": act.get("kind"), "retry_at": act.get("retry_at")}
    put(rec, doc)
    p = subprocess.Popen(["sh", "-c", f"{cmd} >> {shlex.quote(str(out))} 2>&1; echo $? > {shlex.quote(str(ex))}"],
                         cwd=cfg["project"]["runner"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        put(rec, {**doc, "state": "running", "pid": p.pid, "start": lstart(p.pid)})
    except OSError:
        pass                        # the launching record stands; the next tick adopts the process


def notify(title: str, text: str) -> None:
    subprocess.run(["osascript", "-e", f"display notification {json.dumps(text)} with title {json.dumps(title)}"],
                   capture_output=True, timeout=10, check=False)


def backup(cfg: dict, d: Path) -> str | None:
    b = cfg.get("backup")
    stamp = d / "backup.stamp"
    if not b or (stamp.exists() and time.time() - stamp.stat().st_mtime < 86400):
        return None
    dest = Path(b["dest"]).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    for rel in b.get("paths", []):
        src = Path(cfg["project"]["runner"]) / rel
        if src.exists():
            subprocess.run(["rsync", "-a", "--exclude", "*.lock", "--exclude", "*.pid", f"{src}/", f"{dest / rel.strip('/')}/"],
                           capture_output=True, timeout=600, check=False)
    stamp.touch()
    return str(dest)


def tick(cfg: dict, dry: bool = False) -> dict:
    d, runner = state_dir(cfg), cfg["project"]["runner"]
    status = {"at": time.time(), "paused": (d / "paused").exists(), "actions": [], "needs": [], "notes": []}
    lock = d / "tick.lock"
    if not dry:
        try:
            lock.mkdir()
        except FileExistsError:
            if time.time() - lock.stat().st_mtime < 1200:
                return {**status, "notes": ["another tick is running"]}
            lock.rmdir()
            lock.mkdir()
    try:
        if status["paused"]:
            status["notes"].append(f"paused: `fleet resume --config {cfg['_path']}` to start")
            if not dry:  # a dry run still plans, so the operator can preview before resuming
                return status
        if not Path(runner).exists():
            status["notes"].append(f"runner {runner} does not exist")
            return status
        if not dry and "ci_watch" in cfg["steps"]:
            sh(fill(cfg["steps"]["ci_watch"], cfg), runner, timeout=600)
        busy, results = records(d)
        for (sid, step, _), rr in results.items():
            cmd = cfg.get("reconcile", {}).get(step)
            if cmd and not dry and rr["exit"] not in (0, None) and sh(fill(cmd, cfg, story=sid), runner).returncode == 0:
                # the effect already happened (a wrapper died before recording it): record the success
                (d / "runs" / f"{sid}.{step}.exit").write_text("0\n")
                rr["exit"] = 0
                status["notes"].append(f"{sid}: {step} had already happened; not repeated")
        r = sh(fill(cfg["project"]["facts"], cfg), runner, timeout=600)
        if r.returncode:
            status["notes"].append(f"facts failed: {r.stderr.strip()[-400:]}")
            return status
        facts = json.loads(r.stdout)
        lim = cfg.get("limits", {})
        snap = None
        if lim.get("command"):
            lr = sh(fill(lim["command"], cfg), runner, timeout=120)
            try:
                snap = json.loads(lr.stdout) if lr.returncode == 0 else None
            except json.JSONDecodeError:
                snap = None
        cap = capacity(snap, lim)
        if not cap["known"]:
            status["notes"].append("limits unknown: work allowed, the agents' own limit errors still stop it")
        ctx = {"cap": cap, "busy": busy, "results": results, "line_stopped": bool(facts.get("line_stopped")),
               "max_rounds": facts.get("max_rounds", 3), "accounts": cfg.get("vars", {}).get("accounts", ["A", "B"]),
               "allow_start": lim.get("allow_start", True), "now": time.time()}
        acts = plan(facts, ctx)
        running_heavy = sum(1 for steps in busy.values() for s, _ in steps if s in HEAVY)
        acts = heavy_slots(acts, running_heavy, cfg["project"].get("max_parallel_checks", 2))
        heads = {s["id"]: s.get("head") for s in facts["stories"]}
        for a in acts:
            if "need" in a:
                status["needs"].append(a)
            elif "note" in a:
                status["notes"].append(a["note"])
            else:
                status["actions"].append(a)
                if not dry:
                    launch(cfg, d, a, heads.get(a["story"]))
        status["capacity"] = {"known": cap["known"], "left": cap["left"], "codex": cap["codex"]}
        status["in_flight"] = facts.get("in_flight")
        status["line_stopped"] = facts.get("line_stopped")
        if not dry:
            seen_f = d / "notified.json"
            seen = set(json.loads(seen_f.read_text())) if seen_f.exists() else set()
            for n in status["needs"]:
                key = f"{n['story']}:{n['need']}"
                if key not in seen:
                    notify(f"{cfg['project']['name']}: {n['story']} needs you", n["need"])
                    seen.add(key)
            seen_f.write_text(json.dumps(sorted(seen)))
            done = backup(cfg, d)
            if done:
                status["notes"].append(f"backed up to {done}")
        return status
    finally:
        if not dry:
            (d / "status.json").write_text(json.dumps(status, indent=1, default=str))
            try:
                lock.rmdir()
            except OSError:
                # A later tick may have reclaimed this lock as stale; it is already gone.
                pass
            with open(d / "tick.log", "a") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} paused={status['paused']} "
                         f"actions={[(a['story'], a['step'], a.get('reviewer')) for a in status['actions']]} "
                         f"needs={len(status['needs'])} notes={status['notes']}\n")


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    <string>{python}</string><string>-I</string><string>{fleet}</string><string>tick</string>
    <string>--config</string><string>{config}</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{path}</string></dict>
  <key>StartInterval</key><integer>{interval}</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{log}</string><key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""


def label(cfg: dict) -> str:
    return f"com.fleet.{cfg['project']['name']}"


def install(cfg: dict, active: bool) -> None:
    d = state_dir(cfg)
    if not active:
        (d / "paused").touch()
    plist = HOME / "Library" / "LaunchAgents" / f"{label(cfg)}.plist"
    plist.write_text(PLIST.format(
        label=label(cfg), python=sys.executable, fleet=Path(__file__).resolve(), config=cfg["_path"],
        path=f"{HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        interval=cfg["project"].get("interval", 300), log=d / "launchd.log"))
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(plist)], capture_output=True, check=False)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], check=True)
    print(f"installed {plist} ({'active' if active else 'PAUSED'}; every {cfg['project'].get('interval', 300)} s)")


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, opts = argv[0], argv[1:]
    cfg_path = opts[opts.index("--config") + 1] if "--config" in opts else None
    if cmd == "status" and not cfg_path:
        for f in sorted(STATE.glob("*/status.json")):
            s = json.loads(f.read_text())
            print(f"{f.parent.name}: {'PAUSED' if s['paused'] else 'active'} · last tick "
                  f"{time.strftime('%H:%M:%S', time.localtime(s['at']))} · {len(s['actions'])} actions · {len(s['needs'])} needs")
        return 0
    if not cfg_path:
        print("--config <fleet.toml> required", file=sys.stderr)
        return 2
    if not Path(cfg_path).expanduser().exists():
        # the config lives in the project's runner checkout and may not have landed yet (an open PR):
        # `install --name` can still register the agent, paused, and its ticks are no-ops until then
        if cmd in ("install", "pause", "resume") and "--name" in opts:
            cfg = {"project": {"name": opts[opts.index("--name") + 1]}, "_path": str(Path(cfg_path).expanduser())}
        else:
            print(f"no config at {cfg_path} yet; nothing to do")
            return 0
    else:
        cfg = load(cfg_path)
    d = state_dir(cfg)
    if cmd == "tick":
        print(json.dumps(tick(cfg, dry="--dry-run" in opts), indent=1, default=str))
    elif cmd == "status":
        print((d / "status.json").read_text() if (d / "status.json").exists() else "no tick yet")
    elif cmd == "pause":
        (d / "paused").touch()
        print("paused: ticks only watch")
    elif cmd == "resume":
        (d / "paused").unlink(missing_ok=True)
        print("resumed: the next tick acts")
    elif cmd == "install":
        install(cfg, active="--active" in opts)
    elif cmd == "uninstall":
        plist = HOME / "Library" / "LaunchAgents" / f"{label(cfg)}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True, check=False)
        plist.unlink(missing_ok=True)
        print(f"removed {plist}")
    else:
        print(f"unknown command {cmd}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
