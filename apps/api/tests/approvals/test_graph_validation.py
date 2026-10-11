"""APR-006/007/008/012 graph fixtures and regression plants."""

import os
import time
from uuid import uuid4

import pytest

from flo.modules.approvals.schemas import parse_graph
from flo.modules.approvals.validation import check_graph
from tests.approvals.test_workflow_definitions import GRAPH


def approval(id, **changes):
    return {
        "id": id,
        "kind": "approval",
        "mode": "any",
        "approvers": {"type": "role", "role_code": id},
        **changes,
    }


def chain(count):
    nodes = [GRAPH["nodes"][0]] + [approval(f"a{i}") for i in range(count)] + [GRAPH["nodes"][1]]
    return {
        "nodes": nodes,
        "edges": [{"from": a["id"], "to": b["id"]} for a, b in zip(nodes, nodes[1:])],
    }


def report(graph, limit=10):
    return check_graph(
        parse_graph(graph), "requisition", {"a", "b"} | {f"a{i}" for i in range(50)}, set(), limit
    )


def test_cycle_and_cyclic_depth_terminate():
    graph = chain(2)
    graph["edges"] += [{"from": "a1", "to": "a0"}]
    result = report(graph, 1)
    assert [i.node_id for i in result.errors if i.code == "cycle"] == ["a0"]
    assert not any(i.code == "too_deep" for i in result.errors)
    graph["edges"][-1] = {"from": "a1", "to": "a1"}
    assert "cycle" in {i.code for i in report(graph).errors}


def test_unreachable():
    graph = GRAPH | {"nodes": GRAPH["nodes"] + [{"id": "orphan", "kind": "end"}]}
    assert [(i.code, i.node_id) for i in report(graph).errors] == [("unreachable", "orphan")]


def test_dead_end_and_bad_out_degree_and_all_errors():
    graph = chain(1)
    graph["edges"] = graph["edges"][:1]
    assert [(i.code, i.node_id) for i in report(graph).errors] == [
        ("dead_end", "start"),
        ("dead_end", "a0"),
        ("bad_out_degree", "a0"),
        ("unreachable", "end"),
    ]


def condition_graph(when=None):
    graph = {
        "nodes": [GRAPH["nodes"][0], {"id": "c", "kind": "condition"}, GRAPH["nodes"][1]],
        "edges": [{"from": "start", "to": "c"}, {"from": "c", "to": "end", "default": True}],
    }
    if when is not None:
        graph["edges"].append({"from": "c", "to": "end", "when": when})
    return graph


def test_missing_default():
    graph = condition_graph()
    graph["edges"][1].pop("default")
    assert [(i.code, i.node_id) for i in report(graph).errors] == [("missing_default", "c")]
    graph = GRAPH | {"edges": [{"from": "start", "to": "end", "default": True}]}
    assert [(i.code, i.edge_index) for i in report(graph).errors] == [("missing_default", 0)]


@pytest.mark.parametrize(
    "rule", [{"type": "role", "role_code": "absent"}, {"type": "users", "user_ids": [str(uuid4())]}]
)
def test_missing_approvers(rule):
    graph = chain(1)
    graph["nodes"][1]["approvers"] = rule
    assert [(i.code, i.node_id) for i in report(graph).errors] == [("missing_approvers", "a0")]


def test_invalid_condition():
    result = report(condition_graph({"unexpected": True}))
    assert [(i.code, i.edge_index) for i in result.errors] == [("invalid_condition", 2)]


@pytest.mark.parametrize(
    "field,kind,op,value",
    [
        ("custom.absent", None, "eq", "x"),
        ("title", "text", "gt", "3"),
        ("code", "code", "le", "3"),
        ("flag", "boolean", "lt", True),
        ("amount", "money", "eq", "3"),
        ("flag", "boolean", "eq", "true"),
    ],
)
def test_invalid_field(monkeypatch, field, kind, op, value):
    monkeypatch.setattr(
        "flo.modules.approvals.validation.field_catalog", lambda _: {field: kind} if kind else {}
    )
    result = report(condition_graph({"field": field, "op": op, "value": value}))
    assert [(i.code, i.edge_index) for i in result.errors] == [("invalid_field", 2)]


@pytest.mark.parametrize("op", ["lt", "le", "gt", "ge"])
def test_money_ordering_valid(monkeypatch, op):
    monkeypatch.setattr(
        "flo.modules.approvals.validation.field_catalog", lambda _: {"amount": "money"}
    )
    assert not report(
        condition_graph({"field": "amount", "op": op, "value": {"amount": "3", "currency": "USD"}})
    ).errors


def test_depth_boundary_counts_only_approval_nodes():
    assert report(chain(10)).errors == []
    assert report(chain(11)).errors[0].code == "too_deep"
    assert report(chain(3), 3).errors == []
    assert [(i.code, i.node_id) for i in report(chain(4), 3).errors] == [("too_deep", "start")]


@pytest.mark.parametrize("repeat", [False, True])
def test_duplicate_approval(repeat):
    graph = chain(2)
    graph["nodes"][2].update(approvers=graph["nodes"][1]["approvers"], allow_repeat=repeat)
    result = report(graph)
    assert [(i.code, i.node_id) for i in (result.warnings if repeat else result.errors)] == [
        ("duplicate_approval", "a1")
    ]
    assert not (result.errors if repeat else result.warnings)


def test_overlapping_users_permission_and_disjoint_branches():
    user = uuid4()
    for rule in ({"type": "users", "user_ids": [str(user)]}, {"type": "permission"}):
        graph = chain(2)
        for node in graph["nodes"][1:3]:
            node["approvers"] = rule
        result = check_graph(parse_graph(graph), "requisition", set(), {user}, 10)
        assert [i.code for i in result.errors] == ["duplicate_approval"]
    graph = condition_graph()
    graph["nodes"] += [approval("a"), approval("b", approvers={"type": "role", "role_code": "a"})]
    graph["edges"] = [
        {"from": "start", "to": "c"},
        {"from": "c", "to": "a", "default": True},
        {"from": "c", "to": "b"},
        {"from": "a", "to": "end"},
        {"from": "b", "to": "end"},
    ]
    assert not report(graph).errors


def test_quorum_unreachable():
    user = uuid4()
    graph = chain(1)
    graph["nodes"][1].update(
        approvers={"type": "users", "user_ids": [str(user)]}, mode={"quorum": 2}
    )
    result = check_graph(parse_graph(graph), "requisition", set(), {user}, 10)
    assert [(i.code, i.node_id) for i in result.errors] == [("quorum_unreachable", "a0")]


def test_three_errors():
    graph = GRAPH | {
        "nodes": GRAPH["nodes"] + [{"id": f"orphan{i}", "kind": "end"} for i in range(3)]
    }
    assert [i.code for i in report(graph).errors] == ["unreachable"] * 3


@pytest.mark.skipif(os.getenv("FLO_SKIP_TIMING") == "1", reason="Slow runner opted out")
def test_thirty_nodes_twelve_levels_performance():
    graph = chain(12)
    graph["nodes"] += [{"id": f"c{i}", "kind": "condition"} for i in range(16)]
    graph["edges"] = (
        [{"from": "start", "to": "c0"}]
        + [
            {"from": f"c{i}", "to": f"c{i + 1}" if i < 15 else "a0", "default": True}
            for i in range(16)
        ]
        + graph["edges"][1:]
    )
    started = time.perf_counter()
    assert report(graph, 12).errors == []
    assert time.perf_counter() - started < 0.05


@pytest.mark.parametrize(
    "kind,op,value",
    [
        ("decimal", "eq", "3.00"),
        ("decimal", "ge", "3"),
        ("date", "lt", "2026-10-11"),
        ("money", "is_null", None),
        ("date", "not_null", None),
        ("decimal", "is_null", None),
        ("code_list", "contains", "ENG"),
        ("code_list", "not_null", None),
        ("text", "in", ["ENG", "OPS"]),
        ("boolean", "eq", True),
    ],
)
def test_remaining_allowed_operator_sets(monkeypatch, kind, op, value):
    monkeypatch.setattr("flo.modules.approvals.validation.field_catalog", lambda _: {"field": kind})
    when = {"field": "field", "op": op}
    if value is not None:
        when["value"] = value
    assert report(condition_graph(when)).errors == []


@pytest.mark.parametrize(
    "kind,op,value",
    [
        ("decimal", "in", ["3"]),
        ("code_list", "eq", "ENG"),
        ("date", "eq", "not-a-date"),
        ("decimal", "eq", "not-a-number"),
    ],
)
def test_wrong_operator_or_typed_value(monkeypatch, kind, op, value):
    monkeypatch.setattr("flo.modules.approvals.validation.field_catalog", lambda _: {"field": kind})
    result = report(condition_graph({"field": "field", "op": op, "value": value}))
    assert [(i.code, i.edge_index) for i in result.errors] == [("invalid_field", 2)]
