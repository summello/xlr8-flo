"""Install the same concrete templates in API and worker processes."""

from flo.modules.imports.handlers import HANDLERS, register_handler
from flo.modules.imports.templates import REGISTRY, register
from flo.modules.imports.templates.budget_allocations import TEMPLATE as ALLOCATIONS
from flo.modules.imports.templates.budget_allocations import BudgetAllocationsHandler
from flo.modules.imports.templates.projects import TEMPLATE as PROJECTS
from flo.modules.imports.templates.projects import ProjectsHandler


def register_builtin() -> None:
    for template, handler in (
        (PROJECTS, ProjectsHandler()),
        (ALLOCATIONS, BudgetAllocationsHandler()),
    ):
        if template.name not in REGISTRY:
            register(template)
        if template.name not in HANDLERS:
            register_handler(handler)
