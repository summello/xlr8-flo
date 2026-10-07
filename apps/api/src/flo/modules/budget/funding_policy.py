"""Extension point for E06-S04 parent funding policy."""

from decimal import Decimal

from flo.modules.projects.schemas import ProjectRead


def check_allocation(project: ProjectRead, amount: Decimal) -> None:
    """Direct posting until parent funding is implemented in E06-S04."""
