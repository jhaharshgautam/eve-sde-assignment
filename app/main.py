from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import models  # noqa: F401  (register tables)
from app.config import get_settings
from app.database import Base, engine
from app.logging_config import setup_logging
from app.routers import auth, bookings, catalog, payments


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(get_settings().log_level)
    Base.metadata.create_all(engine)  # swap for Alembic migrations in production
    yield


app = FastAPI(
    title="EVE Diagnostics Booking API",
    version="1.0.0",
    description="Diagnostic test bookings with simulated payments and an idempotent webhook.",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(bookings.router)
app.include_router(payments.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
