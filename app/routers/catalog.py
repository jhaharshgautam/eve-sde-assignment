from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.deps import require_admin
from app.models import CentreTest, DiagnosticCentre, DiagnosticTest
from app.schemas import (
    CentreIn,
    CentreOut,
    OfferingIn,
    OfferingOut,
    Page,
    TestIn,
    TestOut,
)

router = APIRouter(tags=["catalogue"])


def _centre_out(c: DiagnosticCentre) -> CentreOut:
    return CentreOut(
        id=c.id,
        name=c.name,
        location=c.location,
        tests=[OfferingOut(test_id=o.test_id, test_name=o.test.name, price=o.price) for o in c.offerings],
    )


def _get_centre(db: Session, centre_id: int) -> DiagnosticCentre:
    c = db.scalar(
        select(DiagnosticCentre)
        .where(DiagnosticCentre.id == centre_id)
        .options(selectinload(DiagnosticCentre.offerings).selectinload(CentreTest.test))
    )
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Centre not found")
    return c


# ---- tests catalogue ----
@router.post("/tests", response_model=TestOut, status_code=201, dependencies=[Depends(require_admin)])
def create_test(body: TestIn, db: Session = Depends(get_db)):
    test = DiagnosticTest(name=body.name, description=body.description)
    db.add(test)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A test with this name already exists")
    return test


@router.get("/tests", response_model=Page[TestOut])
def list_tests(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    total = db.scalar(select(func.count()).select_from(DiagnosticTest)) or 0
    items = db.scalars(select(DiagnosticTest).order_by(DiagnosticTest.id).limit(limit).offset(offset)).all()
    return Page(items=items, total=total, limit=limit, offset=offset)


# ---- centres ----
@router.post("/centres", response_model=CentreOut, status_code=201, dependencies=[Depends(require_admin)])
def create_centre(body: CentreIn, db: Session = Depends(get_db)):
    centre = DiagnosticCentre(name=body.name, location=body.location)
    db.add(centre)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Centre already exists at this location")
    return _centre_out(_get_centre(db, centre.id))


@router.get("/centres", response_model=Page[CentreOut])
def list_centres(
    location: str | None = Query(None, description="Case-insensitive substring match"),
    test_id: int | None = Query(None, description="Only centres offering this test"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(DiagnosticCentre)
    if location:
        stmt = stmt.where(DiagnosticCentre.location.ilike(f"%{location}%"))
    if test_id is not None:
        stmt = stmt.where(
            DiagnosticCentre.id.in_(select(CentreTest.centre_id).where(CentreTest.test_id == test_id))
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.options(selectinload(DiagnosticCentre.offerings).selectinload(CentreTest.test))
        .order_by(DiagnosticCentre.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(items=[_centre_out(c) for c in rows], total=total, limit=limit, offset=offset)


@router.get("/centres/{centre_id}", response_model=CentreOut)
def get_centre(centre_id: int, db: Session = Depends(get_db)):
    return _centre_out(_get_centre(db, centre_id))


@router.put(
    "/centres/{centre_id}/tests",
    response_model=CentreOut,
    dependencies=[Depends(require_admin)],
    summary="Offer a test at a centre (or update its price)",
)
def upsert_offering(centre_id: int, body: OfferingIn, db: Session = Depends(get_db)):
    centre = _get_centre(db, centre_id)
    if db.get(DiagnosticTest, body.test_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Test not found")
    offering = db.scalar(
        select(CentreTest).where(CentreTest.centre_id == centre_id, CentreTest.test_id == body.test_id)
    )
    if offering:
        offering.price = body.price
    else:
        db.add(CentreTest(centre_id=centre_id, test_id=body.test_id, price=body.price))
    db.commit()
    db.expire_all()
    return _centre_out(_get_centre(db, centre.id))
