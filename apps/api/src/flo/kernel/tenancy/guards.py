"""Static CI guards for tenant-safe API inputs and database migrations."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator, Mapping

HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}


def _resolve_ref(document: Mapping[str, object], node: object) -> object:
    if not isinstance(node, dict) or set(node) != {"$ref"}:
        return node
    reference = node["$ref"]
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return node
    resolved: object = document
    for part in reference[2:].split("/"):
        if not isinstance(resolved, dict):
            return node
        resolved = resolved.get(part.replace("~1", "/").replace("~0", "~"))
    return resolved


def _contains_org_id(document: Mapping[str, object], node: object) -> bool:
    node = _resolve_ref(document, node)
    if isinstance(node, list):
        return any(_contains_org_id(document, item) for item in node)
    if not isinstance(node, dict):
        return False
    properties = node.get("properties")
    if isinstance(properties, dict) and "org_id" in properties:
        return True
    return any(_contains_org_id(document, value) for value in node.values())


def forbidden_org_id_operations(document: Mapping[str, object]) -> list[str]:
    """Return operations that allow a caller to supply ``org_id``."""

    violations: list[str] = []
    paths = document.get("paths", {})
    if not isinstance(paths, dict):
        return violations
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        inherited = path_item.get("parameters", [])
        inherited_parameters = inherited if isinstance(inherited, list) else []
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            declared = operation.get("parameters", [])
            declared_parameters = declared if isinstance(declared, list) else []
            parameters = [*inherited_parameters, *declared_parameters]
            bad_parameter = False
            for parameter in parameters:
                parameter = _resolve_ref(document, parameter)
                if not isinstance(parameter, dict):
                    continue
                name = parameter.get("name")
                if isinstance(name, str) and name.lower().replace("-", "_") == "org_id":
                    bad_parameter = True
                    break
            if bad_parameter or _contains_org_id(document, operation.get("requestBody")):
                violations.append(f"{method.upper()} {path}")
    return violations


def _created_tenant_tables(source: str) -> Iterator[str]:
    raw_table = re.compile(
        r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+"
        r"(?P<table>[A-Za-z_][A-Za-z0-9_.]*)\s*\((?P<body>.*?)\)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in raw_table.finditer(source):
        if re.search(r"\borg_id\b", match.group("body"), flags=re.IGNORECASE):
            yield match.group("table").split(".")[-1]

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "create_table":
            continue
        if not call.args or not isinstance(call.args[0], ast.Constant):
            continue
        table = call.args[0].value
        if not isinstance(table, str):
            continue
        strings = {
            node.value
            for node in ast.walk(call)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        if "org_id" in strings:
            yield table


def unprotected_tenant_tables(source: str) -> list[str]:
    """Find tenant tables created without all required RLS statements."""

    normalized = re.sub(r"[\"']", "", source)
    missing: list[str] = []
    for table in sorted(set(_created_tenant_tables(source))):
        escaped = re.escape(table)
        requirements = (
            rf"ALTER\s+TABLE\s+(?:[A-Za-z_][A-Za-z0-9_]*\.)?{escaped}\s+"
            r"ENABLE\s+ROW\s+LEVEL\s+SECURITY",
            rf"ALTER\s+TABLE\s+(?:[A-Za-z_][A-Za-z0-9_]*\.)?{escaped}\s+"
            r"FORCE\s+ROW\s+LEVEL\s+SECURITY",
            rf"CREATE\s+POLICY\s+tenant_isolation\s+ON\s+"
            rf"(?:[A-Za-z_][A-Za-z0-9_]*\.)?{escaped}\b",
        )
        if not all(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in requirements):
            missing.append(table)
    return missing
