"""Pytest plugin: record the shape of every audit row a test writes (E22-S03 golden baseline).

Load with `-p tests.kernel.audit_shapes` and AUDIT_SHAPES_OUT=<file>. The shape is what a
module chose to persist (action, target, actor kind, outcome, reason, snapshot field lists);
ids and timestamps are left out so the shape is stable.
"""

from __future__ import annotations

import json
import os

from flo.kernel.audit import writer as audit_writer

SHAPES: set[str] = set()
_original = audit_writer.AuditWriter.write


def _record(self: object, **kwargs: object) -> object:
    actor = kwargs["actor"]
    outcome = kwargs["outcome"]
    SHAPES.add(
        json.dumps(
            {
                "action": kwargs["action"],
                "target_type": kwargs["target_type"],
                "actor_kind": str(getattr(actor, "kind", None)),
                "outcome": str(outcome),
                "reason": kwargs.get("reason"),
                "before": None
                if kwargs.get("before_source") is None
                else sorted(kwargs.get("before_fields") or ()),  # type: ignore[call-overload]
                "after": None
                if kwargs.get("after_source") is None
                else sorted(kwargs.get("after_fields") or ()),  # type: ignore[call-overload]
                "device": [kwargs.get("ip_address_value") is not None, kwargs.get("user_agent")],
            },
            sort_keys=True,
        )
    )
    return _original(self, **kwargs)  # type: ignore[arg-type]


def pytest_configure(config: object) -> None:
    audit_writer.AuditWriter.write = _record  # type: ignore[method-assign,assignment]


def pytest_unconfigure(config: object) -> None:
    audit_writer.AuditWriter.write = _original  # type: ignore[method-assign]
    out = os.environ["AUDIT_SHAPES_OUT"]
    with open(out, "w") as handle:
        json.dump(sorted(SHAPES), handle)


