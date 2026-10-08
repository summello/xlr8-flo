"""Authorized reads of global FX reference data."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from flo.api.admin_users import organization_target
from flo.api.org import Connection
from flo.kernel.authz import require
from flo.modules.identity.models import AuthorizationContext
from flo.modules.org.schemas import FxRateRead, FxStatus
from flo.modules.org.service import fx_status, rate_for

router = APIRouter(prefix="/api/v1/fx", tags=["fx"])
Read = Annotated[AuthorizationContext, Depends(require("fx.read", organization_target))]
Currency = Annotated[str, Query(pattern=r"^[A-Z]{3}$")]


@router.get("/rates", response_model=FxRateRead)
def rates(
    context: Read, connection: Connection, base: Currency, quote: Currency, on: date
) -> FxRateRead:
    return rate_for(connection, base, quote, on)


@router.get("/status", response_model=FxStatus)
def status(context: Read, connection: Connection) -> FxStatus:
    return fx_status(connection)
