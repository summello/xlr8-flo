"""Authorized adapters for fiscal calendar configuration and period transitions."""

from datetime import timedelta
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request
from starlette.concurrency import run_in_threadpool

from flo.api.admin_users import organization_target
from flo.api.org import Connection
from flo.kernel.authz import AuthorizationTarget as TargetProtocol
from flo.kernel.authz import require
from flo.kernel.session import requires_recent_auth
from flo.kernel.tenancy.context import current_scope
from flo.modules.identity.models import AuthorizationContext, AuthorizationTarget, ScopeType
from flo.modules.org.fiscal import CalendarPut, FiscalPeriod, ReopenBody
from flo.modules.org.service import FiscalService

router = APIRouter(prefix="/api/v1/fiscal", tags=["fiscal"])
Manage = Annotated[AuthorizationContext, Depends(require("fiscal.manage", organization_target))]
Read = Annotated[AuthorizationContext, Depends(require("fiscal.read", organization_target))]
Year = Annotated[int, Path(ge=2, le=9998)]


def period_target(id: UUID, connection: Connection) -> TargetProtocol:
    period = FiscalService(connection, current_scope()).get_period(id)
    return cast(
        TargetProtocol,
        AuthorizationTarget(
            ScopeType.ORG,
            current_scope().org_id,
            "fiscal_period",
            period.id,
        ),
    )


Close = Annotated[AuthorizationContext, Depends(require("fiscal.close", period_target))]
Reopen = Annotated[AuthorizationContext, Depends(require("fiscal.reopen", period_target))]


@router.put("/calendar", response_model=CalendarPut)
def set_calendar(body: CalendarPut, context: Manage, connection: Connection) -> CalendarPut:
    """Configure the fiscal first month before any periods have been generated."""
    return FiscalService(connection, current_scope(), context.user_id).set_calendar(body)


@router.get("/periods", response_model=list[FiscalPeriod])
def list_periods(
    context: Read,
    connection: Connection,
    fiscal_year: Annotated[int, Query(ge=2, le=9998)],
) -> list[FiscalPeriod]:
    """Read generated periods without generating history or locking calendar configuration."""
    return FiscalService(connection, current_scope(), context.user_id).list_periods(fiscal_year)


@router.post("/years/{fiscal_year}:generate", response_model=list[FiscalPeriod])
def generate(fiscal_year: Year, context: Manage, connection: Connection) -> list[FiscalPeriod]:
    """Generate twelve monthly periods, using the year in which the fiscal year ends."""
    return FiscalService(connection, current_scope(), context.user_id).ensure_year(fiscal_year)


@router.post("/periods/{id}:close", response_model=FiscalPeriod)
def close_period(id: UUID, context: Close, connection: Connection) -> FiscalPeriod:
    """Close after the immediately preceding generated period has been closed."""
    return FiscalService(connection, current_scope(), context.user_id).transition(id, "close")


@router.post("/periods/{id}:reopen", response_model=FiscalPeriod)
@requires_recent_auth(max_age=timedelta(minutes=15))
async def reopen_period(
    id: UUID,
    body: ReopenBody,
    context: Reopen,
    connection: Connection,
    request: Request,
) -> FiscalPeriod:
    """Reauthenticate within 15 minutes and explain why; later closed periods block reopening."""
    return await run_in_threadpool(
        FiscalService(connection, current_scope(), context.user_id).transition,
        id,
        "reopen",
        body.reason,
    )
