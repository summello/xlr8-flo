#!/usr/bin/env python3
"""Fleet watch (E23-S19): deterministic detectors over the supervisor's tick.log, a daily digest,
and an append-only log of proposed fixes with their accept/reject outcome. Stdlib only; reads the
state dir, never writes to it."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

DEFAULT_STATE = Path.home() / ".claude/omnissiah/state/xlr8flo"
DEFAULT_OUT = Path(".scratch/watch")
REPEAT_STEP_TICKS = 3
REPEAT_NOTE_TICKS = 5
RESET_STEPS = {"resume", "round", "fix", "submit"}
DETECTORS = ("dup_review", "repeat_step", "repeat_note")
TICK = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) paused=\S+ actions=(\[.*?\]) needs=\d+ notes=(\[.*\])\s*$"
)


def parse_tick(line: str) -> dict | None:
    m = TICK.match(line)
    if not m:
        return None
    try:
        actions = ast.literal_eval(m[2])
        notes = ast.literal_eval(m[3])
        acts = [tuple(a) for a in actions]
        return {"ts": m[1], "line": line, "actions": acts, "notes": [str(n) for n in notes]}
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None


def read_ticks(state: Path, day: str) -> tuple[list[dict], int]:
    ticks, skipped = [], 0
    log = state / "tick.log"
    lines = log.read_text(errors="replace").splitlines() if log.exists() else []
    for line in lines:
        if not line.strip():
            continue
        t = parse_tick(line)
        if t and t["ts"].startswith(day):
            ticks.append(t)
        elif not t and (line[:10] == day or not re.match(r"\d{4}-\d\d-\d\d", line)):
            skipped += 1
    return ticks, skipped


def _finding(detector: str, story: str | None, ticks: list[dict], idx: list[int]) -> dict:
    return {
        "detector": detector,
        "story": story,
        "first_tick": ticks[idx[0]]["ts"],
        "last_tick": ticks[idx[-1]]["ts"],
        "count": len(idx),
        "evidence": [ticks[i]["line"] for i in idx[:2]],
    }


def dup_review(ticks: list[dict]) -> list[dict]:
    out: list[dict] = []
    open_: dict[tuple[str, str], list[int]] = {}

    def flush(key: tuple[str, str]) -> None:
        idx = open_.pop(key, [])
        if len(idx) >= 2:
            out.append(_finding("dup_review", key[0], ticks, idx))

    for i, t in enumerate(ticks):
        for a in t["actions"]:
            if len(a) >= 2 and a[1] in RESET_STEPS:
                for key in [k for k in open_ if k[0] == a[0]]:
                    flush(key)
        for a in t["actions"]:
            if len(a) >= 3 and a[1] == "review":
                idx = open_.setdefault((a[0], a[2]), [])
                if not idx or idx[-1] != i:
                    idx.append(i)
    for key in list(open_):
        flush(key)
    return out


def repeat_step(ticks: list[dict]) -> list[dict]:
    out: list[dict] = []
    runs: dict[tuple[str, str], list[int]] = {}
    for i, t in enumerate(ticks):
        here = {(a[0], a[1]) for a in t["actions"] if len(a) >= 2}
        for key in list(runs):
            if key not in here:
                idx = runs.pop(key)
                if len(idx) >= REPEAT_STEP_TICKS:
                    out.append(_finding("repeat_step", key[0], ticks, idx))
        for key in here:
            runs.setdefault(key, []).append(i)
    for key, idx in runs.items():
        if len(idx) >= REPEAT_STEP_TICKS:
            out.append(_finding("repeat_step", key[0], ticks, idx))
    return out


def repeat_note(ticks: list[dict]) -> list[dict]:
    seen: dict[str, list[int]] = {}
    for i, t in enumerate(ticks):
        for n in {re.sub(r"\d+", "#", n) for n in t["notes"]}:
            seen.setdefault(n, []).append(i)
    return [
        {**_finding("repeat_note", None, ticks, idx), "note": n}
        for n, idx in seen.items()
        if len(idx) >= REPEAT_NOTE_TICKS
    ]


def detect(ticks: list[dict]) -> dict[str, list[dict]]:
    return {
        "dup_review": dup_review(ticks),
        "repeat_step": repeat_step(ticks),
        "repeat_note": repeat_note(ticks),
    }


def render(day: str, ticks: list[dict], skipped: int) -> str:
    out = [f"# Fleet watch digest {day}", ""]
    if not ticks:
        out += [f"No ticks recorded on {day}.", f"Skipped lines: {skipped}", ""]
        return "\n".join(out)
    steps = Counter(a[1] for t in ticks for a in t["actions"] if len(a) >= 2)
    out += [f"Ticks: {len(ticks)}", f"Skipped lines: {skipped}", "Actions by step:"]
    out += [f"- {s}: {n}" for s, n in sorted(steps.items())] or ["- none"]
    for name, findings in detect(ticks).items():
        out += ["", f"## {name}", ""]
        for f in findings:
            out.append(
                f"- story={f['story'] or '-'} count={f['count']}"
                f" {f['first_tick']} .. {f['last_tick']}"
                + (f" note={f['note']!r}" if "note" in f else "")
            )
            out += [f"  - `{e}`" for e in f["evidence"]]
        if not findings:
            out.append("none")
    return "\n".join(out) + "\n"


def cmd_digest(a: argparse.Namespace) -> int:
    ticks, skipped = read_ticks(Path(a.state), a.day)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.day}.md").write_text(render(a.day, ticks, skipped))
    return 0


def _rows(out: Path) -> list[dict]:
    p = out / "proposals.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def _append(out: Path, row: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "proposals.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def cmd_propose(a: argparse.Namespace) -> int:
    out = Path(a.out)
    pid = f"P{sum('text' in r for r in _rows(out)) + 1}"
    _append(
        out,
        {
            "id": pid,
            "ts": _now(),
            "text": a.text,
            "detector": a.detector,
            "story": a.story,
            "status": "open",
        },
    )
    print(pid)
    return 0


def cmd_resolve(a: argparse.Namespace) -> int:
    out = Path(a.out)
    if not any(r["id"] == a.id and "text" in r for r in _rows(out)):
        print(f"unknown proposal id {a.id}", file=sys.stderr)
        return 2
    _append(out, {"id": a.id, "ts": _now(), "status": a.status, "reason": a.reason})
    return 0


def cmd_stats(a: argparse.Namespace) -> int:
    rows = _rows(Path(a.out))
    status = {r["id"]: r["status"] for r in rows}  # latest row wins
    by: dict[str, Counter] = {d: Counter() for d in DETECTORS}
    for r in rows:
        if "text" in r:
            by[r["detector"]][status[r["id"]]] += 1
    for d, c in by.items():
        done = c["accepted"] + c["rejected"]
        rate = round(c["accepted"] / done, 2) if done else "n/a"
        print(
            f"{d}: proposals={sum(c.values())} accepted={c['accepted']} rejected={c['rejected']}"
            f" open={c['open']} rate={rate}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="flo_watch")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("digest")
    d.add_argument("--state", default=str(DEFAULT_STATE))
    d.add_argument("--day", default=date.today().isoformat())
    d.add_argument("--out", default=str(DEFAULT_OUT))
    d.set_defaults(fn=cmd_digest)
    pr = sub.add_parser("propose")
    pr.add_argument("text")
    pr.add_argument("--detector", required=True, choices=DETECTORS)
    pr.add_argument("--story")
    pr.add_argument("--out", default=str(DEFAULT_OUT))
    pr.set_defaults(fn=cmd_propose)
    r = sub.add_parser("resolve")
    r.add_argument("id")
    r.add_argument("status", choices=("accepted", "rejected"))
    r.add_argument("--reason", default="")
    r.add_argument("--out", default=str(DEFAULT_OUT))
    r.set_defaults(fn=cmd_resolve)
    s = sub.add_parser("stats")
    s.add_argument("--out", default=str(DEFAULT_OUT))
    s.set_defaults(fn=cmd_stats)
    a = p.parse_args(argv)
    if a.cmd == "digest":
        try:
            date.fromisoformat(a.day)
        except ValueError:
            print(f"invalid --day {a.day}", file=sys.stderr)
            return 2
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
