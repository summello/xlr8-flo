"""Internal balance read contract."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class BalanceRead:
    project_id: UUID
    org_id: UUID
    bu_id: UUID
    currency: str
    allocated: Decimal
    reserved: Decimal
    committed: Decimal
    actual: Decimal
    available: Decimal
    version: int
    updated_at: datetime
