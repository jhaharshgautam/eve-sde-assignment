import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.deps import get_current_user
from app.models import Booking, BookingStatus, CentreTest, User
from app.schemas import BookingDetail, BookingIn, BookingOut, Page

log = logging.getLogger("bookings")
router = APIRouter(prefix="/bookings", tags=["bookings"])


def _owned_booking(db: Session, booking_id: int, user: User) -> Booking:
    b = db.scalar(select(Booking).where(Booking.id == booking_id).options(selectinload(Booking.payments)))
    # 404 (not 403) for other people's bookings so ids can't be enumerated.
    if b is None or b.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    return b


@router.post("", response_model=BookingOut, status_code=201)
def create_booking(body: BookingIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if body.appointment_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "appointment_at must be in the future")
    offering = db.scalar(
        select(CentreTest).where(CentreTest.centre_id == body.centre_id, CentreTest.test_id == body.test_id)
    )
    if offering is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This centre does not offer that test")
    booking = Booking(
        user_id=user.id,
        centre_test_id=offering.id,
        centre_id=offering.centre_id,
        test_id=offering.test_id,
        appointment_at=body.appointment_at,
        amount=offering.price,
        status=BookingStatus.PENDING,
    )
    db.add(booking)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have a booking for this test and slot")
    log.info("booking_created", extra={"booking_id": booking.id, "user_id": user.id})
    return booking


@router.get("", response_model=Page[BookingOut])
def list_my_bookings(
    status_filter: BookingStatus | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Booking).where(Booking.user_id == user.id)
    if status_filter:
        stmt = stmt.where(Booking.status == status_filter)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(stmt.order_by(Booking.id.desc()).limit(limit).offset(offset)).all()
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{booking_id}", response_model=BookingDetail)
def get_booking(booking_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _owned_booking(db, booking_id, user)


@router.post("/{booking_id}/cancel", response_model=BookingOut)
def cancel_booking(booking_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    booking = _owned_booking(db, booking_id, user)
    if booking.status == BookingStatus.CANCELLED:
        return booking  # idempotent
    if booking.status == BookingStatus.FAILED:
        raise HTTPException(status.HTTP_409_CONFLICT, "A failed booking cannot be cancelled")
    # NOTE: a CONFIRMED (paid) booking would trigger a refund in a real system.
    booking.status = BookingStatus.CANCELLED
    db.commit()
    log.info("booking_cancelled", extra={"booking_id": booking.id})
    return booking
