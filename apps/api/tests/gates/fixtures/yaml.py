"""JSON-only safe_load stand-in for the isolated roadmap command fixture."""

from __future__ import annotations

import json
from typing import IO, Any


def safe_load(stream: IO[str]) -> Any:
    return json.load(stream)
