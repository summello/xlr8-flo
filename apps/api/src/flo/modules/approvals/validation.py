"""Bounded graph validation without enumerating start-to-end paths."""

import re
from datetime import date
from functools import cache
from uuid import UUID

from pydantic import Field

from flo.kernel.errors import ProblemError
from flo.modules.approvals.conditions import field_catalog
from flo.modules.approvals.schemas import (
    AllCondition,
    AnyCondition,
    Condition,
    Contract,
    LeafCondition,
    MoneyValue,
    NotCondition,
    PermissionApprovers,
    Quorum,
    RoleApprovers,
    UserApprovers,
    WorkflowGraph,
    parse_condition,
)


class Issue(Contract):
    code: str
    node_id: str | None = None
    edge_index: int | None = None
    detail: str


class ValidationReport(Contract):
    errors: list[Issue] = Field(default_factory=list)
    warnings: list[Issue] = Field(default_factory=list)


class PublishWrite(Contract):
    acknowledge_warnings: bool = False


def valid_leaf(leaf: LeafCondition, kind: str | None) -> bool:
    # E10-S03's amendment narrows text/code/boolean; the other sets use E10-S02.
    operators = {
        "text": {"eq", "ne", "in", "not_in"},
        "code": {"eq", "ne", "in", "not_in"},
        "boolean": {"eq", "ne", "in", "not_in"},
        "decimal": {"eq", "ne", "lt", "le", "gt", "ge", "is_null", "not_null"},
        "date": {"eq", "ne", "lt", "le", "gt", "ge", "is_null", "not_null"},
        "money": {"eq", "ne", "lt", "le", "gt", "ge", "is_null", "not_null"},
        "code_list": {"contains", "is_null", "not_null"},
    }
    allowed = operators.get(kind or "", set())
    if kind is None or leaf.op not in allowed:
        return False
    if leaf.op in {"is_null", "not_null"}:
        return True
    values = leaf.value if isinstance(leaf.value, list) else [leaf.value]
    for value in values:
        if kind in {"text", "code", "code_list"}:
            valid = isinstance(value, str)
        elif kind == "boolean":
            valid = isinstance(value, bool)
        elif kind == "money":
            valid = isinstance(value, MoneyValue)
        elif kind == "decimal":
            valid = (
                isinstance(value, str)
                and re.fullmatch(r"-?[0-9]{1,14}(\.[0-9]{1,4})?", value) is not None
            )
        else:
            try:
                valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
            except ValueError:
                valid = False
        if not valid:
            return False
    return True


def leaves(condition: Condition) -> list[LeafCondition]:
    if isinstance(condition, (AllCondition, AnyCondition)):
        children = condition.all if isinstance(condition, AllCondition) else condition.any
        return [leaf for child in children for leaf in leaves(child)]
    if isinstance(condition, NotCondition):
        return leaves(condition.not_)
    return [condition]


def check_graph(
    graph: WorkflowGraph, document_type: str, roles: set[str], users: set[UUID], max_depth: int
) -> ValidationReport:
    report = ValidationReport()
    nodes = {node.id: node for node in graph.nodes}
    outgoing: dict[str, list[str]] = {id: [] for id in nodes}
    incoming: dict[str, list[str]] = {id: [] for id in nodes}
    for edge in graph.edges:
        outgoing[edge.from_].append(edge.to)
        incoming[edge.to].append(edge.from_)

    def issue(code: str, id: str, detail: str) -> None:
        report.errors.append(Issue(code=code, node_id=id, detail=detail))

    # Tarjan: one diagnostic per strongly connected component, including self-loops.
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    active: set[str] = set()

    def component(id: str) -> None:
        indices[id] = low[id] = len(indices)
        stack.append(id)
        active.add(id)
        for child in outgoing[id]:
            if child not in indices:
                component(child)
                low[id] = min(low[id], low[child])
            elif child in active:
                low[id] = min(low[id], indices[child])
        if low[id] == indices[id]:
            members = []
            while True:
                member = stack.pop()
                active.remove(member)
                members.append(member)
                if member == id:
                    break
            if len(members) > 1 or id in outgoing[id]:
                issue("cycle", id, "Cycle contains: " + ", ".join(sorted(members)))

    for id in nodes:
        if id not in indices:
            component(id)

    def reachable(seeds: list[str], links: dict[str, list[str]]) -> set[str]:
        seen: set[str] = set()
        pending = seeds[:]
        while pending:
            id = pending.pop()
            if id not in seen:
                seen.add(id)
                pending.extend(links[id])
        return seen

    start = next(n.id for n in graph.nodes if n.kind == "start")
    reached = reachable([start], outgoing)
    finishes = reachable([n.id for n in graph.nodes if n.kind == "end"], incoming)
    for id, node in nodes.items():
        if id not in reached:
            issue("unreachable", id, "Step is not reachable from start.")
        if (node.kind != "end" and not outgoing[id]) or id not in finishes:
            issue("dead_end", id, "Step cannot complete a branch at an end.")
        if node.kind in {"start", "approval"} and len(outgoing[id]) != 1:
            issue("bad_out_degree", id, "Start and approval steps need exactly one outgoing edge.")
        if node.kind == "condition" and sum(e.default for e in graph.edges if e.from_ == id) != 1:
            issue("missing_default", id, "Condition needs exactly one default edge.")
        rules = [node.approvers, node.escalate_to]
        for rule in rules:
            if isinstance(rule, RoleApprovers) and rule.role_code not in roles:
                issue("missing_approvers", id, "Role does not exist in this organization.")
            if isinstance(rule, UserApprovers):
                if not rule.user_ids or not set(rule.user_ids) <= users:
                    issue("missing_approvers", id, "Users must be members of this organization.")
                if rule is node.approvers and isinstance(node.mode, Quorum):
                    if len(set(rule.user_ids)) < node.mode.quorum:
                        issue("quorum_unreachable", id, "Quorum exceeds the number of users.")
    catalog = field_catalog(document_type)
    for index, edge in enumerate(graph.edges):
        if nodes[edge.from_].kind != "condition" and (edge.when is not None or edge.default):
            report.errors.append(
                Issue(
                    code="missing_default",
                    edge_index=index,
                    detail="Only condition edges may carry routing rules.",
                )
            )
        if edge.when is not None:
            try:
                expression = parse_condition(edge.when)
            except ProblemError:
                report.errors.append(
                    Issue(
                        code="invalid_condition",
                        edge_index=index,
                        detail="Condition does not follow the expression grammar.",
                    )
                )
            else:
                for leaf in leaves(expression):
                    if not valid_leaf(leaf, catalog.get(leaf.field)):
                        report.errors.append(
                            Issue(
                                code="invalid_field",
                                edge_index=index,
                                detail=f"Invalid field, operator or value: {leaf.field}.",
                            )
                        )

    if any(i.code == "cycle" for i in report.errors):
        return report

    @cache
    def ancestors(id: str) -> frozenset[str]:
        return frozenset(parent for parent in incoming[id] if parent in reached) | frozenset(
            ancestor
            for parent in incoming[id]
            if parent in reached
            for ancestor in ancestors(parent)
        )

    @cache
    def depth(id: str) -> int:
        return int(nodes[id].kind == "approval") + max(
            (depth(child) for child in outgoing[id] if child in finishes), default=0
        )

    if depth(start) > max_depth:
        issue("too_deep", start, f"Approval depth exceeds the configured limit of {max_depth}.")
    for id, node in nodes.items():
        if node.kind != "approval" or id not in reached or id not in finishes:
            continue
        rule = node.approvers
        for previous in sorted(ancestors(id)):
            prior = nodes[previous]
            if prior.kind != "approval":
                continue
            other = prior.approvers
            repeated = (
                (
                    isinstance(rule, RoleApprovers)
                    and isinstance(other, RoleApprovers)
                    and rule.role_code == other.role_code
                )
                or (
                    isinstance(rule, UserApprovers)
                    and isinstance(other, UserApprovers)
                    and bool(set(rule.user_ids) & set(other.user_ids))
                )
                or (
                    isinstance(rule, PermissionApprovers) and isinstance(other, PermissionApprovers)
                )
            )
            if repeated:
                target = report.warnings if node.allow_repeat else report.errors
                target.append(
                    Issue(
                        code="duplicate_approval",
                        node_id=id,
                        detail=f"Approver rule repeats step {previous}.",
                    )
                )
    return report
