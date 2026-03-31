from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class FeeScheduleBody(BaseModel):
    schedule: dict[str, Any]

