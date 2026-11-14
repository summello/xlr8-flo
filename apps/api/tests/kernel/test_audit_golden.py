"""E22-S03: real module commands persist the same audit rows as before the shared helper.

Each group runs existing tests that drive a replaced `_audit` wrapper through its real command
(HTTP route or service call) and records the shape of every audit row written. The shapes in
audit_golden.json were captured on the code before the wrappers were replaced.
Regenerate deliberately with AUDIT_GOLDEN_WRITE=1.
"""

# ruff: noqa: E501
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).with_name("audit_golden.json")
NOTES = "tests/collaboration/test_notes.py::test_notes_history_if_match_lock_and_idempotency"
INVITE = (
    "tests/identity/test_invitations.py::test_new_accept_session_role_membership_and_single_use"
)
UNITS = "tests/org/test_units.py::test_post_idempotency_replays_response_and_single_audit"
FISCAL = "tests/org/test_fiscal.py::test_close_reopen_order_events_audit_and_step_up"
LIFECYCLE = (
    "tests/requisitions/test_lifecycle.py::test_submit_withdraw_reservation_events_and_cards"
)
AMEND = "tests/requisitions/test_amendments.py::test_non_material_amendment_is_applied_immediately"
GENERATED = "tests/projects/test_generated_project.py::test_generated_project_is_active_marked_audited_and_found"
IMPORT = "tests/imports/test_commit_modes.py::test_atomic_clean_and_idempotency"
WORKFLOW = "tests/approvals/test_workflow_definitions.py::test_writes_audit_and_idempotency"
DECISION = "tests/approvals/test_decisions.py::test_each_decision_records_attribution_audit_and_private_outbox"
REVOKED = "tests/approvals/test_delegation.py::test_revoked_delegate_loses_power_owner_decides_history_unchanged"
REVOKE_ROLE = "tests/authz/test_resolver.py::test_revocation_changes_the_next_check_and_writes_before_after_audit"
ABANDON = "tests/requisitions/test_project_requests.py::test_cancel_after_approval_abandons_keeps_allocation_and_reports_apart"
REASSIGN = "tests/approvals/test_delegation.py::test_admin_reassign_requires_reason_eligibility_and_is_audited"

# One group per replaced wrapper. NOTES also writes identity role, org setting and project rows.
GROUPS = {
    "collaboration": [NOTES],
    "invitation": [INVITE],
    "org": [UNITS],
    "fiscal": [FISCAL],
    "requisitions": [LIFECYCLE],
    "amendments": [AMEND],
    "projects": [
        GENERATED,
        "tests/projects/test_open_documents.py",
        "tests/projects/test_lifecycle.py::test_draft_abandon_uses_closing_rule",
        ABANDON,
    ],
    "identity": [REVOKE_ROLE],
    "imports": [IMPORT],
    "approvals.service": [WORKFLOW],
    "approvals.decisions": [DECISION],
    "approvals.delegation": [REASSIGN, REVOKED],
}


@pytest.mark.parametrize("group", GROUPS)
def test_audit_rows_match_the_pre_helper_baseline(group: str, tmp_path: Path) -> None:
    out = tmp_path / "shapes.json"
    path = os.pathsep.join(filter(None, [os.environ.get("PYTHONPATH"), str(API)]))
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "tests.kernel.audit_shapes", *GROUPS[group]],
        cwd=API,
        env={**os.environ, "AUDIT_SHAPES_OUT": str(out), "PYTHONPATH": path},
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stdout[-2000:]
    shapes = json.loads(out.read_text())
    assert shapes, "the commands wrote no audit rows"
    if os.environ.get("AUDIT_GOLDEN_WRITE"):
        golden = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
        GOLDEN.write_text(json.dumps({**golden, group: shapes}, indent=1, sort_keys=True) + "\n")
        return
    assert shapes == json.loads(GOLDEN.read_text())[group]
