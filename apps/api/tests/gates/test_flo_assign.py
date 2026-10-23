"""`flo assign` picks the author by `author_priority` after eligibility (E23-S04).

There was no test of cmd_assign or author_priority. These pin the order and the rules that make a
reorder safe: a gated story is never given to nemotron, always keeps its Opus final review, and is
never reviewed by its own author.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.gates.flo_loader import load_flo

F = load_flo()
REPO = Path(__file__).resolve().parents[4]
GATED = ["money", "auth", "security", "migration", "concurrency"]
KINDS = ["ledger", "concurrency", "migration", "security", "engine", "api", "chore", "ui", "crud"]


def fleet(priority=("opencode-nemotron", "sonnet", "codex"), nemotron_avoids=True, reviewers=None):
    return {
        "policy": {
            "min_reviewers": 1,
            "author_priority": list(priority),
            "ungated_reviewers": reviewers or ["codex", "qwen", "deepseek"],
            "opus_final_required_tags": GATED,
        },
        "agents": {
            "sonnet": {
                "roles": ["author"],
                "allowed_kinds": KINDS + ["docs", "test"],
                "accounts": {"A": "default"},
                "max_workers_per_account": 2,
            },
            "opencode-nemotron": {
                "roles": ["author"],
                "allowed_kinds": ["crud", "ui", "api", "chore", "docs", "test"],
                "avoid_tags": GATED if nemotron_avoids else [],
                "max_author_size": "M",
            },
            "codex": {"roles": ["author", "review"], "allowed_kinds": KINDS, "metered": "rate"},
            "qwen": {"roles": ["review"], "monthly_budget_usd": 20},
            "deepseek": {"roles": ["review"], "monthly_budget_usd": 20},
        },
    }


@pytest.fixture
def assign(monkeypatch, capsys):
    def run(kind, size, tags=(), config=None, sonnet_free=True):
        story = {
            "id": "E00-S01",
            "t": "T",
            "k": kind,
            "s": size,
            "tags": list(tags),
            "reqs": ["SUP-005"],
            "dep": [],
            "st": "todo",
        }
        roadmap = {
            "milestones": [{"id": "M9", "name": "x", "branch": "b"}],
            "epics": [{"id": "E00", "milestone": "M9", "stories": [story]}],
        }
        monkeypatch.setattr(F, "roadmap", lambda: roadmap)
        monkeypatch.setattr(F, "load_yaml", lambda path: config or fleet())
        monkeypatch.setattr(F, "state", lambda: {"spend": {}})
        monkeypatch.setattr(F, "free_accounts", lambda name, cfg: ["A"] if sonnet_free else [])
        result = F.cmd_assign(["E00-S01"])
        assert json.loads(capsys.readouterr().out) == result
        return result

    return run


def test_ungated_small_crud_goes_to_nemotron_first(assign):
    assert assign("crud", "M")["author"] == "opencode-nemotron"
    assert assign("crud", "S")["author"] == "opencode-nemotron"


def test_the_old_order_would_have_sent_it_to_sonnet(assign):
    old = fleet(priority=("sonnet", "opencode-nemotron", "codex"))
    assert assign("crud", "M", config=old)["author"] == "sonnet"


def test_a_large_story_is_over_nemotrons_size_cap(assign):
    assert assign("crud", "L")["author"] == "sonnet"


def test_gated_work_never_goes_to_nemotron_and_keeps_its_opus_final(assign):
    for tag in GATED:
        out = assign(
            "ui", "S", tags=[tag]
        )  # ui is a kind nemotron may take; only the tag blocks it
        assert out["author"] != "opencode-nemotron", tag
        assert out["opus_final"] is True, tag


def test_without_avoid_tags_nemotron_would_take_gated_work(assign):
    out = assign("ui", "S", tags=["auth"], config=fleet(nemotron_avoids=False))
    assert out["author"] == "opencode-nemotron"


def test_when_sonnet_has_no_free_slot_an_engine_story_goes_to_codex(assign):
    assert assign("engine", "M", sonnet_free=False)["author"] == "codex"


def test_opus_final_does_not_depend_on_who_authors(assign):
    for sonnet_free in (True, False):
        assert assign("ledger", "M", tags=["money"], sonnet_free=sonnet_free)["opus_final"] is True
        assert assign("engine", "M", sonnet_free=sonnet_free)["opus_final"] is False


def test_the_reviewer_is_never_the_author(assign):
    out = assign("engine", "M", sonnet_free=False)
    assert out["author"] == "codex"
    assert out["reviewers"] == ["qwen"]
    assert assign("crud", "M")["reviewers"] == ["codex"]


def test_no_independent_reviewer_stops_the_assignment(assign):
    with pytest.raises(SystemExit):
        assign("engine", "M", config=fleet(reviewers=["codex"]), sonnet_free=False)


def test_the_committed_config_has_sonnet_only_authoring():
    text = (REPO / "agents" / "agents.yaml").read_text()
    assert re.search(r"^  author_priority: \[sonnet\]$", text, re.M)

    def roles(name: str) -> list[str]:
        block = re.search(
            rf"^  {re.escape(name)}:\n(?P<body>.*?)(?=^  \S+:\n|\Z)", text, re.M | re.S
        )
        assert block, f"agent block not found: {name}"
        line = re.search(r"^    roles: \[(.*)\]", block.group("body"), re.M)
        assert line, f"roles line not found: {name}"
        return [role.strip() for role in line.group(1).split(",") if role.strip()]

    assert "author" in roles("sonnet")
    for name in ("codex", "opencode-nemotron", "qwen", "kimi"):
        assert "author" not in roles(name)
