"""Fleet watch detectors, digest and proposals log (agents/scripts/flo_watch.py, E23-S19)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[4] / "agents" / "scripts"
spec = importlib.util.spec_from_file_location("flo_watch", SCRIPTS / "flo_watch.py")
assert spec is not None and spec.loader is not None
W = importlib.util.module_from_spec(spec)
sys.modules["flo_watch"] = W
spec.loader.exec_module(W)

DAY = "2026-10-30"


def tick(
    n: int, actions: list[tuple[str, ...]] | None = None, notes: list[str] | None = None
) -> str:
    return (
        f"{DAY} 10:{n:02d}:00 paused=False actions={actions or []!r} needs=0 notes={notes or []!r}"
    )


def state(tmp_path: Path, lines: list[str]) -> Path:
    s = tmp_path / "state"
    s.mkdir(exist_ok=True)
    (s / "tick.log").write_text("\n".join(lines) + "\n")
    return s


def found(tmp_path: Path, lines: list[str], detector: str) -> list[dict]:
    ticks, _ = W.read_ticks(state(tmp_path, lines), DAY)
    return W.detect(ticks)[detector]


REV = ("E13-S08", "review", "codex")


def test_dup_review_flagged(tmp_path: Path) -> None:
    assert len(found(tmp_path, [tick(1, [REV]), tick(2), tick(3, [REV])], "dup_review")) == 1


def test_dup_review_not_flagged_with_round_between(tmp_path: Path) -> None:
    lines = [tick(1, [REV]), tick(2, [("E13-S08", "round", "")]), tick(3, [REV])]
    assert found(tmp_path, lines, "dup_review") == []


def test_repeat_step_three_consecutive(tmp_path: Path) -> None:
    a = ("E13-S08", "submit")
    f = found(tmp_path, [tick(1, [a]), tick(2, [a]), tick(3, [a])], "repeat_step")
    assert len(f) == 1 and f[0]["count"] == 3 and len(f[0]["evidence"]) == 2


def test_repeat_step_two_or_non_consecutive_not_flagged(tmp_path: Path) -> None:
    a = ("E13-S08", "finish")
    assert found(tmp_path, [tick(1, [a]), tick(2, [a])], "repeat_step") == []
    lines = [tick(1, [a]), tick(2, [a]), tick(3), tick(4, [a])]
    assert found(tmp_path, lines, "repeat_step") == []


def test_repeat_note_collapses_digits(tmp_path: Path) -> None:
    lines = [tick(i, notes=[f"waiting on {i} items"]) for i in range(1, 6)]
    assert len(found(tmp_path, lines, "repeat_note")) == 1
    assert found(tmp_path, lines[:4], "repeat_note") == []


def test_clean_digest_says_none(tmp_path: Path) -> None:
    ticks, skipped = W.read_ticks(state(tmp_path, [tick(1), tick(2)]), DAY)
    text = W.render(DAY, ticks, skipped)
    assert text.count("\nnone") == 3


def test_malformed_line_is_skipped_and_digest_renders(tmp_path: Path) -> None:
    s = state(
        tmp_path,
        [tick(1), f"{DAY} 10:02:00 paused=False actions=[(oops needs=0 notes=[]", "garbage"],
    )
    out = tmp_path / "out"
    assert W.main(["digest", "--state", str(s), "--day", DAY, "--out", str(out)]) == 0
    assert "Skipped lines: 2" in (out / f"{DAY}.md").read_text()


def test_digest_for_empty_day_exit_zero(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert (
        W.main(
            [
                "digest",
                "--state",
                str(state(tmp_path, [tick(1)])),
                "--day",
                "2026-01-01",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert "No ticks recorded" in (out / "2026-01-01.md").read_text()


def test_digest_idempotent_and_state_untouched(tmp_path: Path) -> None:
    s = state(tmp_path, [tick(1, [REV]), tick(2, [REV])])
    (s / "runs").mkdir()
    (s / "runs" / "E13-S08.review.codex.json").write_text("{}")
    before = {p: p.read_bytes() for p in s.rglob("*") if p.is_file()}
    out = tmp_path / "out"
    args = ["digest", "--state", str(s), "--day", DAY, "--out", str(out)]
    W.main(args)
    first = (out / f"{DAY}.md").read_text()
    W.main(args)
    assert (out / f"{DAY}.md").read_text() == first
    assert {p: p.read_bytes() for p in s.rglob("*") if p.is_file()} == before
    assert {p for p in s.rglob("*") if p.is_file()} == set(before)


def test_proposals_flow_and_append_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = str(tmp_path / "w")
    assert (
        W.main(
            [
                "propose",
                "dedupe reviews",
                "--detector",
                "dup_review",
                "--story",
                "E11-S16",
                "--out",
                out,
            ]
        )
        == 0
    )
    log = Path(out) / "proposals.jsonl"
    first = log.read_text()
    assert W.main(["resolve", "P1", "accepted", "--reason", "ok", "--out", out]) == 0
    assert log.read_text().startswith(first)  # earlier rows unchanged
    capsys.readouterr()
    W.main(["stats", "--out", out])
    assert (
        "dup_review: proposals=1 accepted=1 rejected=0 open=0 rate=1.0" in capsys.readouterr().out
    )
    W.main(["stats", "--out", out])
    assert (
        "repeat_step: proposals=0 accepted=0 rejected=0 open=0 rate=n/a" in capsys.readouterr().out
    )


def test_resolve_unknown_id_exits_2_and_appends_nothing(tmp_path: Path) -> None:
    out = str(tmp_path / "w")
    W.main(["propose", "x", "--detector", "repeat_note", "--out", out])
    before = (Path(out) / "proposals.jsonl").read_text()
    assert W.main(["resolve", "P9", "accepted", "--out", out]) == 2
    with pytest.raises(SystemExit) as e:
        W.main(["resolve", "P1", "maybe", "--out", out])
    assert e.value.code == 2
    assert (Path(out) / "proposals.jsonl").read_text() == before
