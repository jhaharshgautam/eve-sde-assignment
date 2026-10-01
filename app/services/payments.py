"""Payment + booking state transitions. All status changes go through here."""
import logging
import random
import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Booking, BookingStatus, Payment, PaymentStatus, WebhookEvent

log = logging.getLogger("payments")

PAYABLE_BOOKING_STATES = {BookingStatus.PENDING, BookingStatus.FAILED}


class PaymentError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail


def _apply_payment_result(booking: Booking, payment: Payment, status: PaymentStatus, reason: str | None) -> str:
    """Move a PENDING payment to a final state and sync the booking. Returns outcome label."""
    payment.status = status
    payment.failure_reason = reason if status == PaymentStatus.FAILED else None
    if booking.status == BookingStatus.CANCELLED:
        # Money moved on a cancelled booking: keep booking cancelled, flag for refund handling.
        log.warning("payment_on_cancelled_booking", extra={"booking_id": booking.id, "payment_id": payment.id})
        return "applied"
    if status == PaymentStatus.SUCCESS:
        booking.status = BookingStatus.CONFIRMED
    elif booking.status == BookingStatus.PENDING:
        booking.status = BookingStatus.FAILED
    return "applied"


def create_payment(
    db: Session,
    booking_id: int,
    user_id: int,
    simulate_outcome: str | None,
    async_mode: bool,
) -> Payment:
    # Row lock serialises concurrent pay attempts (no-op on SQLite).
    booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
    if booking is None or booking.user_id != user_id:
        # Same response for "missing" and "not yours" so ids can't be probed.
        raise PaymentError(404, "Booking not found")
    if booking.status not in PAYABLE_BOOKING_STATES:
        raise PaymentError(409, f"Booking is {booking.status.value} and cannot be paid")
    in_flight = any(p.status == PaymentStatus.PENDING for p in booking.payments)
    if in_flight:
        raise PaymentError(409, "A payment for this booking is already in progress")

    payment = Payment(
        booking_id=booking.id,
        provider_reference=f"sim_{secrets.token_hex(12)}",
        amount=booking.amount,
        status=PaymentStatus.PENDING,
    )
    db.add(payment)
    if not async_mode:
        if simulate_outcome is None:
            ok = random.random() < get_settings().mock_payment_success_rate
        else:
            ok = simulate_outcome == "SUCCESS"
        _apply_payment_result(
            booking,
            payment,
            PaymentStatus.SUCCESS if ok else PaymentStatus.FAILED,
            None if ok else "Simulated card decline",
        )
    db.commit()
    log.info(
        "payment_created",
        extra={"payment_id": payment.id, "booking_id": booking.id, "status": payment.status.value},
    )
    return payment


def process_webhook(
    db: Session, event_id: str, provider_reference: str, status: str, reason: str | None
) -> tuple[str, Payment, Booking]:
    """Idempotent. Returns (result, payment, booking); result in applied|duplicate|ignored."""
    payment = db.scalar(
        select(Payment).where(Payment.provider_reference == provider_reference).with_for_update()
    )
    if payment is None:
        raise PaymentError(404, "Unknown payment reference")
    booking = db.get(Booking, payment.booking_id)
    reported = PaymentStatus(status)

    if db.scalar(select(WebhookEvent.id).where(WebhookEvent.event_id == event_id)) is not None:
        log.info("webhook_duplicate", extra={"event_id": event_id})
        return "duplicate", payment, booking

    if payment.status == PaymentStatus.PENDING:
        outcome = _apply_payment_result(booking, payment, reported, reason)
    else:
        # Final states are terminal: a late/out-of-order/conflicting event can't flip them.
        outcome = "ignored"
        log.warning(
            "webhook_ignored_terminal_payment",
            extra={"event_id": event_id, "payment_id": payment.id, "current": payment.status.value, "reported": status},
        )

    db.add(WebhookEvent(event_id=event_id, payment_id=payment.id, reported_status=reported, outcome=outcome))
    try:
        db.commit()
    except IntegrityError:
        # Lost a race with a concurrent delivery of the same event -> treat as duplicate.
        db.rollback()
        payment = db.get(Payment, payment.id)
        return "duplicate", payment, db.get(Booking, payment.booking_id)
    log.info("webhook_processed", extra={"event_id": event_id, "outcome": outcome, "payment_id": payment.id})
    return outcome, payment, booking
