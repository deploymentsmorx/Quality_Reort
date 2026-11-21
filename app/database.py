from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
load_dotenv(ROOT / ".env")


def database_url() -> str:
    configured = os.environ.get("DATABASE_URL", "").strip()
    if configured:
        return configured
    DATA.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + (DATA / "ie_inspection.db").as_posix()


def database_info(url: str | None = None) -> dict:
    url = url or database_url()
    if url.startswith("sqlite"):
        return {
            "driver": "sqlite",
            "label": "Local SQLite (this PC)",
            "detail": str(DATA / "ie_inspection.db"),
            "lightsail": False,
        }
    host = url.split("@")[-1] if "@" in url else url
    return {
        "driver": "postgres",
        "label": "Lightsail DB",
        "detail": host.split("?")[0],
        "lightsail": True,
    }


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


def session_factory(engine: Engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
