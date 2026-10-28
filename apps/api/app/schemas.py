"""Request and response models. Unknown fields are rejected; the organization is never an input."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

VendorStatus = Literal["active", "archived"]


def _no_control_characters(value: str) -> str:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("must not contain control characters")
    return value


VendorName = Annotated[
    str,
    Field(min_length=1, max_length=200),
    AfterValidator(_no_control_characters),
]


class VendorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: VendorName
    status: VendorStatus = "active"


class VendorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: VendorName | None = None
    status: VendorStatus | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> VendorUpdate:
        if not self.model_fields_set or (self.name is None and self.status is None):
            raise ValueError("provide at least one of: name, status")
        return self


class VendorOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    status: VendorStatus
    created_at: datetime
    updated_at: datetime


class VendorPage(BaseModel):
    items: list[VendorOut]
    limit: int
    offset: int
