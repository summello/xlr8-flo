"""Pure rules behind `flo facts` and `flo round`: what each in-flight story's files say about it.

`flo facts` prints these facts as JSON for the user-scoped fleet supervisor (~/.claude/fleet), which
decides the next step for each story. The supervisor knows nothing about this repository; this
module is where the repository's conventions (logs ending in `exit N`, pidfiles, verdict files,
round files) are read. No I/O here: callers pass text and file names in.
"""

from __future__ import annotations

import re

# An author run that stopped on the account's usage window, or lost the network, is resumed later;
# it did not fail. Matched on the log's last lines only: this product has rate-limiting stories,
# and an author's report about them must not read as the author being limited.
LIMIT = re.compile(
    r"hit your (?:session |usage |weekly )?limit|usage limit reached|session limit"
    r"|ENOTFOUND|ECONNRESET",
    re.IGNORECASE,
)
EXIT = re.compile(r"^exit (\d+)\s*$", re.MULTILINE)
ROUND = re.compile(r"\.round(\d+)\.md$")

APPROVE = {"approve", "approved", "lgtm"}
CHANGES = {"changes_requested", "changes-requested", "request_changes", "changes requested",
           "reject", "rejected"}


def author_result(log: str | None) -> str:
    """none (never ran) | running (no exit line yet) | ok | limit | failed."""
    if log is None:
        return "none"
    codes = EXIT.findall(log)
    if not codes:
        return "running"
    if codes[-1] == "0":
        return "ok"
    return "limit" if LIMIT.search(log[-600:]) else "failed"


def verdict_state(review: dict | None, reviewed_sha: str | None, submitted_sha: str | None) -> str:
    """approve | changes | stale (reviewed an earlier commit) | missing | malformed."""
    if review is None:
        return "missing"
    if not submitted_sha or reviewed_sha != submitted_sha:
        return "stale"
    v = str(review.get("verdict") or "").strip().lower()
    if v in APPROVE:
        return "approve"
    if v in CHANGES:
        # Nothing itemised means nothing to send the author: a reviewer stopped at its cap with a
        # draft (E22-S02 round 3). "malformed" asks the operator; "missing" would relaunch the reviewer
        # every tick if it keeps writing an empty verdict, because reviews have no attempt counter.
        return "changes" if review.get("findings") else "malformed"
    return "malformed"


def ci_red(marker: str | None, submitted_sha: str | None) -> bool:
    """A .ci-red marker counts only for the commit it names (its first line). A re-submit leaves the
    old marker until the new run completes, and reading it as red resumes the author to fix a run
    that the milestone merge in that re-submit may already have fixed."""
    return bool(marker and submitted_sha and submitted_sha in marker.split("\n", 1)[0])


def open_blockers(reviews: list[dict]) -> list[dict]:
    """Blocker findings still open: every blocker except those Opus dismissed in arbitration
    (`"arbitration": "dismissed"`, AGENTS.md §2.3; the scorecard counts them against the reviewer)."""
    return [f for r in reviews for f in r.get("findings", [])
            if f.get("severity") == "blocker" and f.get("arbitration") != "dismissed"]


def rounds_done(names: list[str], sid: str) -> int:
    """Highest round number among .scratch/<sid>.roundN.md files."""
    nums = [int(m.group(1)) for n in names if n.startswith(f"{sid}.") and (m := ROUND.search(n))]
    return max(nums, default=0)


FINDING_LINE = re.compile(r"^\d+\. \(", re.MULTILINE)


def is_void_round(text: str) -> bool:
    """A round file with no numbered finding: round_file() writes exactly that for an empty list."""
    return not FINDING_LINE.search(text)


def void_rounds(texts: list[str]) -> int:
    """Round files that sent the author nothing to fix. Not charged as rounds."""
    return sum(1 for t in texts if is_void_round(t))


SEVERITY = {"blocker": 0, "major": 1, "minor": 2}


def round_file(sid: str, n: int, verdicts: dict[str, dict]) -> str:
    """The findings a round sends back to the author's session, most severe first."""
    rows = []
    for who, review in sorted(verdicts.items()):
        if str(review.get("verdict") or "").strip().lower() not in CHANGES:
            continue
        for f in review.get("findings") or []:
            rows.append((SEVERITY.get(str(f.get("severity")), 3), who, f))
    rows.sort(key=lambda r: (r[0], r[1]))
    lines = [
        f"REVIEW ROUND {n} for {sid}. Fix every blocker and major below in this worktree;",
        "fix a minor when it is cheap. For a finding you dispute, say why in your report instead",
        "of changing the code. Then re-run `flo check --light` and the touched test files, update",
        f'traceability, and commit with messages starting "{sid}:". No submit, push or merge.',
        "",
    ]
    for i, (_, who, f) in enumerate(rows, 1):
        where = f"{f.get('file', '?')}:{f.get('line', '?')}"
        req = f", {f['requirement']}" if f.get("requirement") else ""
        problem = f.get("problem", "").strip()
        lines.append(f"{i}. ({f.get('severity', '?')}{req}, {who}) {where}: {problem}")
        if f.get("fix"):
            lines.append(f"   Fix: {str(f['fix']).strip()}")
    if not rows:
        lines.append("(The reviewers requested changes without itemised findings: "
                     "read their verdict files' summaries.)")
    return "\n".join(lines) + "\n"
