from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_number: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    part_number: Mapped[str] = mapped_column(String(120), default="")
    part_name: Mapped[str] = mapped_column(String(200), default="")
    revision: Mapped[str] = mapped_column(String(40), default="")
    drawing_number: Mapped[str] = mapped_column(String(120), default="")
    customer: Mapped[str] = mapped_column(String(160), default="")
    work_order: Mapped[str] = mapped_column(String(80), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    mass: Mapped[str] = mapped_column(String(80), default="")
    report_date: Mapped[str] = mapped_column(String(40), default="")
    company_name: Mapped[str] = mapped_column(String(200), default="")
    material: Mapped[str] = mapped_column(String(120), default="")
    finish_treatment: Mapped[str] = mapped_column(String(120), default="")
    serial_number: Mapped[str] = mapped_column(String(80), default="")
    supplier_name: Mapped[str] = mapped_column(String(160), default="")
    authorized_signatory: Mapped[str] = mapped_column(String(160), default="")
    doc_number: Mapped[str] = mapped_column(String(80), default="")
    client_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rows: Mapped[list["BalloonRow"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="BalloonRow.sort_index",
    )
    uploads: Mapped[list["Upload"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )


class BalloonRow(Base):
    __tablename__ = "balloon_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer, default=1)
    work_area: Mapped[str] = mapped_column(String(120), default="")
    balloon_no: Mapped[str] = mapped_column(String(40), default="")
    characteristic: Mapped[str] = mapped_column(String(240), default="")
    nominal: Mapped[str] = mapped_column(String(80), default="")
    tol_plus: Mapped[float | None] = mapped_column(Float, nullable=True)
    tol_minus: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), default="mm")
    instrument: Mapped[str] = mapped_column(String(120), default="")
    instrument_id: Mapped[str] = mapped_column(String(80), default="")
    m1: Mapped[str] = mapped_column(String(40), default="")
    m2: Mapped[str] = mapped_column(String(40), default="")
    m1_confident: Mapped[bool] = mapped_column(Boolean, default=True)
    m2_confident: Mapped[bool] = mapped_column(Boolean, default=True)
    m1_source: Mapped[str] = mapped_column(String(20), default="manual")
    m2_source: Mapped[str] = mapped_column(String(20), default="manual")
    sheet_result: Mapped[str] = mapped_column(String(80), default="")
    ocr_note: Mapped[str] = mapped_column(Text, default="")
    sort_index: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="rows")


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    asset_id: Mapped[str] = mapped_column(String(80), default="")
    range_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    range_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), default="mm")


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(240))
    purpose: Mapped[str] = mapped_column(String(20), default="report")
    stored_path: Mapped[str] = mapped_column(String(400))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="uploads")


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    users: Mapped[list["User"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class Tablet(Base):
    __tablename__ = "tablets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    device_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    detected_name: Mapped[str] = mapped_column(String(160), default="")
    detected_platform: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20), default="registered")
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    client: Mapped["Client"] = relationship()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(40), default="quality")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    client: Mapped["Client | None"] = relationship(back_populates="users")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
