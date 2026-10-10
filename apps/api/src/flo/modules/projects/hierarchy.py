"""Scoped recursive traversals; each operation executes one bounded statement."""

from uuid import UUID

from flo.kernel.errors import ErrorCode, ProblemError
from flo.modules.projects.models import ProjectRepository
from flo.modules.projects.schemas import PathRead, ProjectRef

FIELDS = ("id", "parent_id", "root_id", "number", "name", "status", "depth")
COLUMNS = ", ".join("p." + field for field in FIELDS)


class Hierarchy:
    def __init__(self, repo: ProjectRepository) -> None:
        self.repo = repo

    def walk(self, ids: list[UUID], *, upward: bool) -> dict[UUID, list[ProjectRef]]:
        edge = "p.id = walk.parent_id" if upward else "p.parent_id = walk.id"
        rows = self.repo.execute(
            f"""WITH RECURSIVE walk AS (
            SELECT p.id AS origin, {COLUMNS}, 1 AS depth_guard
            FROM project p WHERE p.org_id = %(org_id)s AND p.id = ANY(%(ids)s)
            UNION
            SELECT walk.origin, {COLUMNS}, walk.depth_guard + 1
            FROM walk JOIN project p ON {edge} AND p.org_id = %(org_id)s
            WHERE depth_guard < 6
            ), unique_nodes AS (
            SELECT DISTINCT ON (origin, id) * FROM walk
            ORDER BY origin, id, depth_guard
            ) SELECT origin, id, parent_id, root_id, number, name, status, depth
            FROM unique_nodes ORDER BY origin, depth_guard, number, id""",
            {"ids": ids},
        ).fetchall()
        result: dict[UUID, list[ProjectRef]] = {id: [] for id in ids}
        for row in rows:
            origin = UUID(str(row[0]))
            result[origin].append(
                ProjectRef.model_validate(dict(zip(FIELDS, row[1:], strict=True)))
            )
        if any(not nodes for nodes in result.values()):
            raise ProblemError(ErrorCode.NOT_FOUND)
        return result

    def ancestors(self, id: UUID) -> list[ProjectRef]:
        return self.walk([id], upward=True)[id][1:]

    def descendants(self, id: UUID, include_self: bool = True) -> list[ProjectRef]:
        nodes = self.walk([id], upward=False)[id]
        return nodes if include_self else nodes[1:]

    def common_path(self, a: UUID, b: UUID) -> tuple[ProjectRef | None, PathRead]:
        paths = self.walk([a, b], upward=True)
        a_nodes, b_nodes = paths[a], paths[b]
        b_ids = {node.id for node in b_nodes}
        lca = next((node for node in a_nodes if node.id in b_ids), None)
        if lca is None:
            return None, PathRead(up=[], lca=None, down=[])
        up = [
            node.id
            for node in a_nodes[: next(i for i, node in enumerate(a_nodes) if node.id == lca.id)]
        ]
        down = [
            node.id
            for node in b_nodes[: next(i for i, node in enumerate(b_nodes) if node.id == lca.id)]
        ]
        return lca, PathRead(up=up, lca=lca.id, down=down[::-1])
