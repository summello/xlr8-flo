"""Stage durations in the scorecard record (E23-S01).

A missing or impossible input must omit its key: a recorded 0 would pull every median down.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from tests.gates.flo_loader import load_flo

F = load_flo()
ROOT = Path(__file__).resolve().parents[4]
START = "2026-10-22T10:00:00+0000"
SUBMIT = "2026-10-22T10:30:00+0000"
DONE = "2026-10-22T12:00:00+0000"


def packet(**over):
    base = {"started_at": START, "submitted_at": SUBMIT, "done_at": DONE, "gate_seconds": 40}
    base.update(over)
    return base


def test_all_inputs_present_gives_every_duration():
    out = F.durations_from(packet(), {"codex": 1100.0}, {"codex": 1000.0})
    assert out == {"wall_s": 7200, "author_s": 1800, "review_s": {"codex": 100}, "gate_s": 40}


def test_missing_submitted_at_omits_author_s_not_zero():
    pkt = packet()
    del pkt["submitted_at"]
    out = F.durations_from(pkt, {}, {})
    assert "author_s" not in out
    assert out["wall_s"] == 7200


def test_done_before_start_omits_wall_and_warns():
    warnings: list[str] = []
    out = F.durations_from(packet(done_at="2026-10-22T09:00:00+0000"), {}, {}, warn=warnings.append)
    assert "wall_s" not in out
    assert any("wall_s" in w for w in warnings)


def test_review_without_a_start_marker_is_omitted():
    out = F.durations_from(packet(), {"codex": 1100.0, "qwen": 1200.0}, {"codex": 1000.0})
    assert out["review_s"] == {"codex": 100}


def test_nothing_known_gives_empty_dict():
    assert F.durations_from({}, {}, {}) == {}


def test_report_counts_coverage_and_hides_small_cells():
    def rec(story, kind, secs):
        return {
            "story": story,
            "kind": kind,
            "author": "sonnet",
            "durations": {
                "wall_s": secs,
                "author_s": secs // 2,
                "gate_s": 30,
                "review_s": {"codex": 60},
            },
        }

    plain = {"story": "OLD-1", "kind": "crud", "author": "codex"}
    sc = {
        "min_sample": 3,
        "stories": [
            rec("A", "chore", 600),
            rec("B", "chore", 1200),
            rec("C", "chore", 1800),
            rec("D", "ui", 3000),
            plain,
        ],
    }
    out = F.render_durations(sc)
    assert "4 of 5 records carry durations" in out
    chore = next(ln for ln in out.splitlines() if ln.startswith("chore"))
    ui = next(ln for ln in out.splitlines() if ln.startswith("ui"))
    assert "20.0" in chore  # median of 600, 1200, 1800 seconds is 20 minutes
    assert "—" in ui  # one sample is under min_sample


def test_report_leaves_committed_scorecard_files_untouched():
    scorecard = ROOT / "agents" / "scorecard.json"
    score_md = ROOT / "agents" / "SCORECARD.md"
    before = (scorecard.read_bytes(), score_md.read_bytes())
    F.render_durations(json.loads(scorecard.read_text()))
    assert (scorecard.read_bytes(), score_md.read_bytes()) == before


def test_now_iso_round_trips_through_epoch():
    assert abs(F._epoch(F.now_iso()) - time.time()) < 5
