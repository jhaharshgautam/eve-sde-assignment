import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import PaymentIn, PaymentOut, WebhookIn, WebhookOut
from app.security import verify_webhook_signature
from app.services import payments as svc

log = logging.getLogger("payments.api")
router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/", response_model=PaymentOut, status_code=201)
def pay(body: PaymentIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Mock payment: resolves to SUCCESS/FAILED (random unless `simulate_outcome` is given)."""
    try:
        return svc.create_payment(db, body.booking_id, user.id, body.simulate_outcome, body.async_mode)
    except svc.PaymentError as e:
        raise HTTPException(e.status_code, e.detail)


@router.post("/webhook/", response_model=WebhookOut)
async def webhook(
    request: Request,
    x_webhook_signature: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Provider callback. HMAC-SHA256 signed, idempotent on `event_id`.

    Always 200 for duplicates so the provider stops retrying.
    """
    raw = await request.body()
    if not verify_webhook_signature(raw, x_webhook_signature):
        log.warning("webhook_bad_signature")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")
    try:
        event = WebhookIn.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as e:
        raise HTTPException(422, f"Invalid webhook payload: {e}")
    try:
        result, payment, booking = svc.process_webhook(
            db, event.event_id, event.provider_reference, event.status, event.failure_reason
        )
    except svc.PaymentError as e:
        raise HTTPException(e.status_code, e.detail)
    return WebhookOut(result=result, payment_status=payment.status, booking_status=booking.status)
