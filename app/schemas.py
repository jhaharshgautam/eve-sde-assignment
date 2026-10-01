from datetime import datetime
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.models import BookingStatus, PaymentStatus

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Price = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ---- auth ----
class SignupIn(BaseModel):
    email: EmailStr
    full_name: Name
    password: Annotated[str, StringConstraints(min_length=8, max_length=128)]

    @field_validator("email")
    @classmethod
    def lower(cls, v: str) -> str:
        return v.lower()


class LoginIn(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def lower(cls, v: str) -> str:
        return v.lower()


class UserOut(ORM):
    id: int
    email: EmailStr
    full_name: str
    is_admin: bool


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


# ---- catalogue ----
class TestIn(BaseModel):
    name: Name
    description: str | None = Field(default=None, max_length=500)


class TestOut(ORM):
    id: int
    name: str
    description: str | None


class CentreIn(BaseModel):
    name: Name
    location: Name


class OfferingIn(BaseModel):
    test_id: int
    price: Price


class OfferingOut(BaseModel):
    test_id: int
    test_name: str
    price: Decimal


class CentreOut(ORM):
    id: int
    name: str
    location: str
    tests: list[OfferingOut] = []


# ---- bookings ----
class BookingIn(BaseModel):
    centre_id: int
    test_id: int
    appointment_at: datetime

    @field_validator("appointment_at")
    @classmethod
    def must_be_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("appointment_at must include a timezone offset, e.g. 2026-11-01T10:00:00+05:30")
        return v


class PaymentOut(ORM):
    id: int
    booking_id: int
    provider_reference: str
    amount: Decimal
    status: PaymentStatus
    failure_reason: str | None
    created_at: datetime


class BookingOut(ORM):
    id: int
    user_id: int
    centre_id: int
    test_id: int
    appointment_at: datetime
    amount: Decimal
    status: BookingStatus
    created_at: datetime


class BookingDetail(BookingOut):
    payments: list[PaymentOut] = []


# ---- payments ----
class PaymentIn(BaseModel):
    booking_id: int
    # Test hook for the mock gateway: force an outcome instead of random.
    simulate_outcome: Literal["SUCCESS", "FAILED"] | None = None
    # If true the payment stays PENDING and is finalised later via the webhook.
    async_mode: bool = False


class WebhookIn(BaseModel):
    event_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    provider_reference: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    status: Literal["SUCCESS", "FAILED"]
    failure_reason: str | None = Field(default=None, max_length=255)


class WebhookOut(BaseModel):
    result: Literal["applied", "duplicate", "ignored"]
    payment_status: PaymentStatus
    booking_status: BookingStatus
