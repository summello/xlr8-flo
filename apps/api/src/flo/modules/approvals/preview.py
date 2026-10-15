"""One pure route walk and presentation words for its taken branches."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import cast

from flo.kernel.errors import ErrorCode, ProblemError
from flo.kernel.money import quantize
from flo.modules.approvals import conditions
from flo.modules.approvals.schemas import (
    AllCondition,
    AnyCondition,
    AttributeValue,
    Condition,
    LeafCondition,
    MoneyValue,
    Node,
    WorkflowGraph,
    parse_condition,
)


def conflict(problem: str, detail: str) -> ProblemError:
    return ProblemError(ErrorCode.CONFLICT, checks={"problem": problem}, detail=detail)


def describe_branch(condition: Condition | None, taken: bool) -> str:
    if not taken or condition is None:
        return "No other condition matched"
    if isinstance(condition, LeafCondition):
        words = {
            "eq": "is",
            "ne": "is not",
            "lt": "is below",
            "le": "is at most",
            "gt": "is over",
            "ge": "is at least",
            "in": "is one of",
            "not_in": "is not one of",
            "contains": "contains",
            "is_null": "is empty",
            "not_null": "is not empty",
        }
        value = condition.value
        rendered = (
            f"{quantize(Decimal(value.amount), value.currency):,} {value.currency}"
            if isinstance(value, MoneyValue)
            else conditions.render(value)
        )
        label = (
            "Estimate"
            if condition.field == "amount"
            else condition.field.replace("_", " ").capitalize()
        )
        if condition.op in {"is_null", "not_null"}:
            return f"{label} {words[condition.op]}"
        return f"{label} {words[condition.op]} {rendered}"
    if isinstance(condition, AllCondition):
        return " and ".join(describe_branch(c, True) for c in condition.all)
    if isinstance(condition, AnyCondition):
        return " or ".join(describe_branch(c, True) for c in condition.any)
    return f"Not ({describe_branch(condition.not_, True)})"


@dataclass(frozen=True)
class WalkResult:
    node: Node
    path_node_ids: tuple[str, ...]
    path_edge_indices: tuple[int, ...]
    reached_via: str | None


def walk(
    graph: WorkflowGraph,
    attributes: Mapping[str, AttributeValue],
    from_node: str | None = None,
    route: list[dict[str, object]] | None = None,
) -> WalkResult:
    route = route if route is not None else []
    reached_via = next(
        (
            cast(str | None, entry["reached_via"])
            for entry in reversed(route)
            if "reached_via" in entry
        ),
        None,
    )
    from_node = from_node or next(n.id for n in graph.nodes if n.kind == "start")
    path = [from_node]
    path_edges: list[int] = []
    nodes = {n.id: n for n in graph.nodes}
    node = nodes[from_node]
    while node.kind != "end":
        edges = [e for e in graph.edges if e.from_ == node.id]
        chosen = None
        if node.kind == "condition":
            first_trace = len(route)
            for edge in edges:
                if edge.when is None:
                    continue
                try:
                    evaluation = conditions.evaluate(parse_condition(edge.when), attributes)
                except conditions.ConditionError as exc:
                    raise conflict(
                        "route_blocked",
                        f"Approval routing is blocked at {exc.field}. Ask a workflow "
                        f"administrator.",
                    ) from exc
                route.append(
                    {
                        "condition_node_id": node.id,
                        "to": edge.to,
                        "result": evaluation.result,
                        "trace": [asdict(t) for t in evaluation.trace],
                    }
                )
                if evaluation.result:
                    chosen = edge
                    break
            chosen = chosen or next((e for e in edges if e.default), None)
        else:
            chosen = edges[0] if edges else None
        if chosen is None:
            raise conflict(
                "route_blocked",
                f"Approval routing is blocked at {node.name or node.id}. Ask a "
                f"workflow administrator.",
            )
        if node.kind == "condition":
            reached_via = describe_branch(
                parse_condition(chosen.when) if chosen.when is not None else None,
                not chosen.default,
            )
            if len(route) == first_trace:
                route.append(
                    {"condition_node_id": node.id, "to": chosen.to, "result": False, "trace": []}
                )
            route[-1].update(
                taken_branch=chosen.when, default=chosen.default, reached_via=reached_via
            )
        path.append(chosen.to)
        path_edges.append(graph.edges.index(chosen))
        node = nodes[chosen.to]
        if node.kind == "approval":
            return WalkResult(node, tuple(path), tuple(path_edges), reached_via)
    return WalkResult(node, tuple(path), tuple(path_edges), reached_via)
