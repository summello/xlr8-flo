"""Code-owned, versioned templates; custom fields remain empty until M4."""

from flo.kernel.errors import ErrorCode, ProblemError
from flo.modules.imports.schemas import Template

REGISTRY: dict[str, Template] = {}


def register(template: Template) -> None:
    if template.name in REGISTRY:
        raise ValueError("template already registered")
    names = [column.name for column in template.columns]
    if len(names) != len(set(names)) or not set(template.key_columns) <= set(names):
        raise ValueError("invalid template columns")
    REGISTRY[template.name] = template


def get_template(name: str) -> Template:
    template = REGISTRY.get(name)
    if template is None:
        raise ProblemError(ErrorCode.NOT_FOUND)
    return template


def approved_custom_fields() -> tuple[str, ...]:
    return ()
