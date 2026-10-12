"""Delivery status, derived from git rather than from one checkout's roadmap.yaml.

Why this exists: `board.html` used to be rendered by whichever checkout ran `flo done`, from that
checkout's own `agents/roadmap.yaml`. The M2 runner works in its own checkout, so the board the
operator opens (the parent repo) never saw its merges, and a milestone merged to `main` kept
showing as in flight. Here every fact comes from refs and worktrees, which are shared by all
checkouts of the repository, and the result is written once to `<repo>/.flo/status.json`.

Story states (one per story, first match wins):

    deployed     its `E00-S00:` commit is reachable from origin/main
    merged       its commit is on a milestone branch (flo done) but not yet on main
    committed    a story worktree holds commits ahead of the milestone and the required
                 reviewers have approved: only `flo done` is left
    in_progress  a story worktree exists, or the roadmap says in_progress / review
    ready        the design packet exists and nothing has started
    todo         nothing exists yet

Epic and milestone states are deliberately coarse: todo, on_train, ready_to_merge, deployed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

STORY_STATES = ("todo", "ready", "in_progress", "committed", "merged", "deployed")
STORY_LABEL = {
    "todo": "To Do", "ready": "Ready", "in_progress": "In Progress",
    "committed": "Committed", "merged": "Merged", "deployed": "Deployed",
}
GROUP_STATES = ("todo", "on_train", "ready_to_merge", "deployed")
GROUP_LABEL = {
    "todo": "To Do", "on_train": "On Train", "ready_to_merge": "Ready to Merge",
    "deployed": "Deployed",
}
# Roadmap `st` values map onto the six states only for the facts the roadmap alone can give.
_ROADMAP_RANK = {"todo": 0, "design": 0, "in_progress": 1, "review": 1, "blocked": 1, "done": 2}
_STORY_ID = re.compile(r"^(E\d\d-S\d\d):")


# ── pure rules (unit-tested) ──────────────────────────────────────────────────

def story_state(facts):
    """facts: deployed, merged, committed, in_progress, packet (all optional booleans)."""
    for key, state in (("deployed", "deployed"), ("merged", "merged"),
                       ("committed", "committed"), ("in_progress", "in_progress"),
                       ("packet", "ready")):
        if facts.get(key):
            return state
    return "todo"


def group_state(states, ci=None):
    """Roll story states up to todo | on_train | ready_to_merge | deployed.

    Returns (state, note). `ci` is the milestone branch's CI result: success, failure, pending
    or None when it could not be read. Ready to Merge needs every story merged AND green CI;
    anything else with all stories merged stays On Train and says why.
    """
    states = list(states)
    if not states:
        return "todo", None
    if all(s == "deployed" for s in states):
        return "deployed", None
    if all(s in ("merged", "deployed") for s in states):
        if ci == "success":
            return "ready_to_merge", None
        return "on_train", f"all stories merged; CI {ci or 'unknown'}"
    if any(s in ("in_progress", "committed", "merged", "deployed") for s in states):
        return "on_train", None
    return "todo", None


def counts(states):
    out = {s: 0 for s in STORY_STATES}
    for s in states:
        out[s] += 1
    return out


def ci_from_runs(runs, head_sha=None):
    """Collapse the newest push's workflow runs into success | failure | pending | None."""
    if not runs:
        return None
    sha = head_sha or runs[0].get("headSha")
    latest = [r for r in runs if r.get("headSha") == sha]
    if not latest:
        return "pending"  # the branch moved on and nothing has run for its head yet
    if any(r.get("status") != "completed" for r in latest):
        return "pending"
    bad = {"failure", "cancelled", "timed_out", "startup_failure", "action_required"}
    if any(r.get("conclusion") in bad for r in latest):
        return "failure"
    return "success"


def deploy_verdict(story_id, history):
    """Did this story's code reach production? history: main's CI runs, newest first, each
    {sha, status, conclusion, deploy, ids}. `deploy` is success | failure | pending | skipped.

    A story shipped if ANY run containing its commit deployed successfully, whatever happened to
    later runs. Failed runs only count while none succeeded. A story no run in the window contains
    predates it, so it is treated as shipped. A run whose deploy job was skipped (a change that
    needs no deploy) ships nothing and fails nothing.
    """
    covering = [r for r in history if story_id in r["ids"]]
    if not covering:
        return "success"
    outcomes = [r["deploy"] for r in covering]
    for want in ("success", "pending", "failure"):
        if want in outcomes:
            return want
    return "success" if any(r["conclusion"] == "success" for r in covering) else "pending"


def approvals_ok(reviews, task):
    """Reviewer floor and the Opus final, as `flo gate` applies them (min_reviewers is 1)."""
    ok = [r for r in reviews
          if r.get("ran_tests") and str(r.get("verdict", "")).lower().startswith("approv")]
    if not ok:
        return False
    return not (task.get("opus_final") and not any(r.get("reviewer") == "opus" for r in ok))


# ── git plumbing ──────────────────────────────────────────────────────────────

def _env():
    env = dict(os.environ)
    # The cache volume named in these variables is unmounted on the operator's Mac; gh and git
    # helpers fail through it. See agents/scripts/env.sh.
    cache = Path.home() / ".cache"
    value = env.get("XDG_CACHE_HOME", "")
    if value and not Path(value).exists():
        env["XDG_CACHE_HOME"] = str(cache)
    return env


def _run(args, cwd, timeout=30):
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env=_env(), check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def main_root(any_checkout):
    """The repository's primary working directory, whichever worktree we were started from."""
    out = _run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], any_checkout)
    return Path(out.strip()).parent if out else Path(any_checkout)


def _refs(root, patterns):
    out = _run(["git", "for-each-ref", "--format=%(refname:short)", *patterns], root) or ""
    return [line for line in out.splitlines() if line]


def _show(root, ref, path):
    return _run(["git", "show", f"{ref}:{path}"], root)


def _subjects(root, ref):
    out = _run(["git", "log", "--format=%s", ref], root, timeout=60) or ""
    return {m.group(1) for m in (_STORY_ID.match(line) for line in out.splitlines()) if m}


def _ls_design(root, ref):
    out = _run(["git", "ls-tree", "--name-only", ref, "design/"], root) or ""
    return {Path(p).stem for p in out.splitlines() if re.fullmatch(r"design/E\d\d-S\d\d\.md", p)}


def _worktrees(root):
    out = _run(["git", "worktree", "list", "--porcelain"], root) or ""
    found, cur = [], {}
    for line in out.splitlines() + [""]:
        if not line:
            if cur.get("branch"):
                found.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur["path"] = line[9:]
        elif line.startswith("branch "):
            cur["branch"] = line[7:].removeprefix("refs/heads/")
    return found


def _load_yaml(text):
    try:
        return json.loads(text)  # JSON is YAML; lets tests and tools avoid PyYAML
    except ValueError:
        import yaml  # PyYAML is a flo dependency; run flo with `uv run --with pyyaml`

        return yaml.safe_load(text)


def gh_ci(root, branch, head_sha):
    runs = _run(["gh", "run", "list", "--branch", branch, "--limit", "12", "--json",
                 "status,conclusion,headSha,name,url"], root, timeout=25)
    if runs is None:
        return None
    try:
        return ci_from_runs(json.loads(runs), head_sha)
    except ValueError:
        return None


def _deploy_of(jobs):
    for j in jobs:
        if j.get("name") == "deploy":
            c = j.get("conclusion")
            if c in ("success", "skipped"):
                return c
            if c in ("failure", "cancelled", "timed_out", "startup_failure"):
                return "failure"
            return "pending"
    return "skipped"


def main_history(root, main_ref):
    """CI runs on main, newest first, with their deploy-job result. None when gh is unavailable."""
    out = _run(["gh", "run", "list", "--branch", "main", "--workflow", "ci.yml", "--limit", "10",
                "--json", "databaseId,headSha,status,conclusion"], root, timeout=25)
    try:
        runs = json.loads(out) if out else None
    except ValueError:
        runs = None
    if runs is None:
        return None
    cache_file = Path(root) / ".flo" / "runs.json"
    try:
        cache = json.loads(cache_file.read_text())
    except (OSError, ValueError):
        cache = {}
    done = cache.setdefault("runs", {})
    ids = cache.setdefault("ids", {})
    history = []
    for r in runs:
        key = str(r["databaseId"])
        if key not in done:
            raw = _run(["gh", "run", "view", key, "--json", "jobs"], root, timeout=25)
            try:
                jobs = json.loads(raw)["jobs"] if raw else None
            except (ValueError, KeyError):
                jobs = None
            if jobs is None:
                continue
            verdict = _deploy_of(jobs) if r["status"] == "completed" else "pending"
            if r["status"] == "completed":
                done[key] = verdict
            else:
                done.pop(key, None)
        else:
            verdict = done[key]
        sha = r["headSha"]
        if sha not in ids:
            ids[sha] = sorted(_subjects(root, sha))
        history.append({"sha": sha, "status": r["status"], "conclusion": r.get("conclusion"),
                        "deploy": verdict, "ids": set(ids[sha])})
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache))
    except OSError:
        pass
    return history


def gh_pr(root, branch):
    out = _run(["gh", "pr", "list", "--head", branch, "--state", "all", "--limit", "1", "--json",
                "number,state,isDraft,url,mergeStateStatus"], root, timeout=25)
    try:
        rows = json.loads(out) if out else []
    except ValueError:
        return None
    return rows[0] if rows else None


# ── gather ────────────────────────────────────────────────────────────────────

def gather(root, use_gh=True, fetch=False, history=None):
    """Read refs, worktrees and (optionally) GitHub, and return the status document.

    `history` injects main's CI runs (tests); otherwise they are read from GitHub when use_gh.
    """
    root = Path(root)
    try:
        prev = json.loads((root / ".flo" / "status.json").read_text())
    except (OSError, ValueError):
        prev = {}
    if fetch:
        _run(["git", "fetch", "--quiet", "--prune", "origin"], root, timeout=60)
    ms_refs = _refs(root, ["refs/heads/milestone", "refs/remotes/origin/milestone"])
    doc_refs = _refs(root, ["refs/heads/docs", "refs/remotes/origin/docs"])
    main_ref = "origin/main" if _refs(root, ["refs/remotes/origin/main"]) else "main"

    # Roadmap: structure from the ref that knows the most stories, status from the furthest ref.
    best, best_n, rank = None, -1, {}
    for ref in [main_ref, *ms_refs]:
        text = _show(root, ref, "agents/roadmap.yaml")
        if not text:
            continue
        try:
            rm = _load_yaml(text)
        except Exception:  # noqa: BLE001, S112 - a ref with an unreadable roadmap is skipped
            continue
        if not isinstance(rm, dict):
            continue
        n = sum(len(e.get("stories", [])) for e in rm.get("epics", []))
        if n > best_n:
            best, best_n = rm, n
        for e in rm.get("epics", []):
            for s in e.get("stories", []):
                rank[s["id"]] = max(rank.get(s["id"], 0), _ROADMAP_RANK.get(s.get("st"), 0))
    if best is None:
        return {"version": 1, "error": "no agents/roadmap.yaml on any ref", "stories": {},
                "epics": {}, "milestones": {}, "counts": counts([])}

    merged = set().union(*[_subjects(root, r) for r in ms_refs]) if ms_refs else set()
    on_main = _subjects(root, main_ref)
    if history is None and use_gh:
        history = main_history(root, main_ref)
    # on main is not enough: its deploy run must have succeeded (when we can see the runs)
    verdict = {sid: (deploy_verdict(sid, history) if history is not None else None) for sid in on_main}
    deployed = {sid for sid in on_main if history is None or verdict[sid] == "success"}
    packets = set()
    for ref in [main_ref, *ms_refs, *doc_refs]:
        packets |= _ls_design(root, ref)

    wt = {}
    for w in _worktrees(root):
        m = re.match(r"story/(E\d\d-S\d\d)-", w["branch"])
        if m:
            wt[m.group(1)] = w

    ms_branch = {m["id"]: m["branch"] for m in best["milestones"]}
    stories = {}
    for e in best["epics"]:
        for s in e["stories"]:
            sid = s["id"]
            facts = {
                "deployed": sid in deployed,
                "merged": sid in merged or sid in on_main or rank.get(sid, 0) >= 2,
                "packet": sid in packets,
                "in_progress": rank.get(sid, 0) == 1 or sid in wt,
            }
            task, blocked, branch = {}, False, None
            if sid in wt and not facts["merged"]:
                path = Path(wt[sid]["path"])
                branch = wt[sid]["branch"]
                try:
                    task = json.loads((path / "task.json").read_text())
                except (OSError, ValueError):
                    task = {}
                blocked = bool((task.get("notes") or {}).get("blocked"))
                base = task.get("milestone_branch") or ms_branch.get(e["milestone"], "")
                ahead = _run(["git", "rev-list", "--count", f"{base}..{branch}"], root) if base else None
                reviews = []
                for f in sorted((path / "reviews").glob(f"{sid}.*.json")):
                    try:
                        reviews.append(json.loads(f.read_text()))
                    except (OSError, ValueError):
                        pass
                facts["committed"] = (ahead or "0").strip() != "0" and approvals_ok(reviews, task)
            stories[sid] = {
                "title": s["t"], "epic": e["id"], "milestone": e["milestone"],
                "kind": s.get("k"), "size": s.get("s"), "tags": s.get("tags", []),
                "dep": s.get("dep", []), "state": story_state(facts), "blocked": blocked,
                "author": task.get("author"), "reviewers": task.get("reviewers"),
                "rounds": task.get("rounds"), "branch": branch,
                "on_main": sid in on_main, "deploy": verdict.get(sid),
            }

    milestones, epics, ci_of = {}, {}, {}
    for m in best["milestones"]:
        ids = [sid for sid, v in stories.items() if v["milestone"] == m["id"]]
        states = [stories[i]["state"] for i in ids]
        ci, pr = None, None
        started = any(s in ("merged", "committed", "in_progress") for s in states)
        if use_gh and started and not all(s == "deployed" for s in states):
            head = (_run(["git", "rev-parse", f"origin/{m['branch']}"], root) or "").strip() or None
            ci = gh_ci(root, m["branch"], head)
            pr = gh_pr(root, m["branch"])
        was = (prev.get("milestones") or {}).get(m["id"]) or {}
        if use_gh and started and not all(s == "deployed" for s in states):
            ci = ci if ci is not None else was.get("ci")
            pr = pr if pr is not None else was.get("pr")
        elif not use_gh:
            ci, pr = was.get("ci"), was.get("pr")
        ci_of[m["id"]] = ci
        state, note = group_state(states, ci)
        mine = [stories[i] for i in ids]
        if mine and all(v["on_main"] for v in mine) and not all(v["state"] == "deployed" for v in mine):
            bad = {v["deploy"] for v in mine if v["state"] != "deployed"}
            state = "on_train"
            note = "on main; deploy " + ("failed" if "failure" in bad else "running")
        milestones[m["id"]] = {
            "name": m["name"], "branch": m["branch"], "state": state, "note": note,
            "total": len(ids), "counts": counts(states), "ci": ci, "pr": pr,
        }
    for e in best["epics"]:
        ids = [sid for sid, v in stories.items() if v["epic"] == e["id"]]
        states = [stories[i]["state"] for i in ids]
        state, note = group_state(states, ci_of.get(e["milestone"]))
        epics[e["id"]] = {
            "name": e["name"], "milestone": e["milestone"], "state": state, "note": note,
            "total": len(ids), "counts": counts(states),
        }
    main = None
    if history:
        newest = next((r for r in history if r["deploy"] != "skipped"), history[0])
        main = {"head": history[0]["sha"][:8], "deploy": newest["deploy"],
                "status": history[0]["status"]}
    return {
        "version": 1,
        "deploy_check": "ok" if history is not None else "unavailable",
        "main": main,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(root),
        "stories": stories, "epics": epics, "milestones": milestones,
        "counts": counts([v["state"] for v in stories.values()]),
    }


def write_status(root, doc):
    """Atomic write, so a reader (the Mission Control pane) never sees half a file."""
    out = Path(root) / ".flo" / "status.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    tmp.replace(out)
    return out


def fetch_due(root, every=120):
    """True when the last fetch stamp is older than `every` seconds (and records this one)."""
    stamp = Path(root) / ".flo" / "last-fetch"
    stamp.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - stamp.stat().st_mtime < every:
            return False
    except OSError:
        pass
    stamp.write_text(str(time.time()))
    return True


def best_json(root, path, key):
    """The copy of a tracked JSON file with the most entries under `key`, across main and the
    milestone branches (a checkout may be behind the branch the runner has been merging into)."""
    best, n = None, -1
    refs = ["origin/main", *_refs(root, ["refs/heads/milestone", "refs/remotes/origin/milestone"])]
    for ref in refs:
        text = _show(root, ref, path)
        if not text:
            continue
        try:
            obj = json.loads(text)
        except ValueError:
            continue
        if len(obj.get(key, [])) > n:
            best, n = obj, len(obj.get(key, []))
    return best
