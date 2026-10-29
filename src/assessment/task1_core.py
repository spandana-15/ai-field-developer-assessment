"""Pure, testable domain and Pydantic validation logic for the stdio MCP server."""
from __future__ import annotations

import math
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


CUSTOMERS = {
    "CUST-00001": {"customer_id": "CUST-00001", "name": "Alex Morgan", "status": "active"},
    "CUST-00002": {"customer_id": "CUST-00002", "name": "Taylor Brooks", "status": "active"},
}

CustomerId = Annotated[str, Field(pattern=r"^CUST-[0-9]{5}$", strict=True)]


class CustomerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    customer_id: CustomerId


class RefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: CustomerId
    amount: float = Field(gt=0, allow_inf_nan=False)
    reason: str = Field(strict=True, min_length=10)

    @field_validator("amount", mode="before")
    @classmethod
    def reject_non_numeric_amount(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("amount must be a JSON number")
        if not math.isfinite(value):
            raise ValueError("amount must be finite")
        return value

    @field_validator("reason")
    @classmethod
    def reject_blank_reason(cls, value: str) -> str:
        if len(value.strip()) < 10:
            raise ValueError("reason must contain at least 10 non-whitespace characters")
        return value


def get_customer_record(request: CustomerRequest) -> dict:
    if request.customer_id not in CUSTOMERS:
        raise LookupError("Customer not found")
    return dict(CUSTOMERS[request.customer_id])


def trigger_refund(request: RefundRequest) -> dict:
    if request.customer_id not in CUSTOMERS:
        raise LookupError("Customer not found")
    # Assessment mock only: this does NOT issue a real financial transaction.
    return {
        "refund_id": "REF-" + uuid4().hex[:12].upper(),
        "customer_id": request.customer_id,
        "amount": request.amount,
        "status": "simulated",
        "reason": request.reason,
    }
