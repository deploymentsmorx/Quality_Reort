from __future__ import annotations

import platform
import re
import secrets
import time
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app.auth import PRESET_ROLES, aware, hash_password, role_label, session_expiry, verify_password
from app.checks import evaluate
from app.database import DATA, database_info, database_url, make_engine, session_factory
from app.exports import build_csv, build_pdf, build_xlsx, filter_rows
from app.models import AuthSession, BalloonRow, Base, Client, Instrument, Job, Tablet, Upload, User, utcnow
from app.ocr_pipeline import (
    anthropic_config,
    apply_extraction,
    load_settings,
    read_document,
    save_settings,
)
from app.schemas import ClientIn, DeviceIn, ExportLinkIn, InstrumentIn, JobIn, LoginIn, RowIn, SaveIn, SettingsIn, TabletIn, UserIn
from app.seal import save_seal
from app.seed import seed

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
UPLOADS = DATA / "uploads"

engine = make_engine()
SessionLocal = session_factory(engine)
Base.metadata.create_all(engine)


def ensure_job_columns() -> None:
    inspector = inspect(engine)
    if "jobs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("jobs")}
    needed = {
        "mass": "VARCHAR(80) DEFAULT ''",
        "report_date": "VARCHAR(40) DEFAULT ''",
        "company_name": "VARCHAR(200) DEFAULT ''",
        "material": "VARCHAR(120) DEFAULT ''",
        "finish_treatment": "VARCHAR(120) DEFAULT ''",
        "serial_number": "VARCHAR(80) DEFAULT ''",
        "supplier_name": "VARCHAR(160) DEFAULT ''",
        "authorized_signatory": "VARCHAR(160) DEFAULT ''",
        "doc_number": "VARCHAR(80) DEFAULT ''",
        "client_id": "INTEGER",
    }
    with engine.begin() as connection:
        for name, column_type in needed.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {column_type}"))


def ensure_row_columns() -> None:
    inspector = inspect(engine)
    if "balloon_rows" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("balloon_rows")}
    if "sheet_result" not in existing:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE balloon_rows ADD COLUMN sheet_result VARCHAR(80) DEFAULT ''"))


def ensure_tablet_columns() -> None:
    inspector = inspect(engine)
    if "tablets" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("tablets")}
    if "model" not in existing:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE tablets ADD COLUMN model VARCHAR(120) DEFAULT ''"))


ensure_job_columns()
ensure_row_columns()
ensure_tablet_columns()
with SessionLocal() as session:
    seed(session)

app = FastAPI(title="IE Inspection")
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

_actor: ContextVar[dict | None] = ContextVar("actor", default=None)
PUBLIC_API = {"/api/login", "/api/logout", "/api/device", "/api/license/check"}
EXPORT_KINDS = {"blank.pdf", "export.xlsx", "export.pdf", "export.csv"}
EXPORT_LINK_TTL = 600
_export_links: dict[str, dict] = {}


def _api_is_public(path: str) -> bool:
    return path in PUBLIC_API or path.startswith("/api/export/")


def _prune_export_links() -> None:
    now = time.time()
    for token in [key for key, item in _export_links.items() if item["expires"] < now]:
        _export_links.pop(token, None)


def db_session() -> Session:
    return SessionLocal()


def actor() -> dict:
    user = _actor.get()
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return user


def _public_user(user: User, client_name: str = "") -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "role": user.role,
        "role_label": role_label(user.role),
        "client_id": user.client_id,
        "client_name": client_name,
    }


def _user_from_token(token: str) -> dict | None:
    with db_session() as session:
        row = session.scalar(select(AuthSession).where(AuthSession.token == token))
        if not row or aware(row.expires_at) <= utcnow():
            return None
        user = session.get(User, row.user_id)
        if not user or not user.active:
            return None
        client_name = ""
        if user.client_id:
            client = session.get(Client, user.client_id)
            client_name = client.name if client else ""
        return _public_user(user, client_name)


def _actor_for_user_id(user_id: int) -> dict:
    with db_session() as session:
        user = session.get(User, user_id)
        if not user or not user.active:
            raise HTTPException(status_code=403, detail="Export not allowed.")
        client_name = ""
        if user.client_id:
            client = session.get(Client, user.client_id)
            client_name = client.name if client else ""
        return _public_user(user, client_name)


@app.middleware("http")
async def require_sign_in(request: Request, call_next):
    user = None
    if request.url.path.startswith("/api/") and not _api_is_public(request.url.path):
        token = request.cookies.get("ie_session", "")
        user = _user_from_token(token) if token else None
        if user is None:
            return JSONResponse({"detail": "Sign in required."}, status_code=401)
    token_ctx = _actor.set(user)
    try:
        return await call_next(request)
    finally:
        _actor.reset(token_ctx)


def _require_super() -> dict:
    user = actor()
    if user["role"] != "super_admin":
        raise HTTPException(status_code=403, detail="Only the super admin can do this.")
    return user


def _require_client_admin() -> dict:
    user = actor()
    if user["role"] != "client_admin" or not user["client_id"]:
        raise HTTPException(status_code=403, detail="Only a client admin can do this.")
    return user


def _username_owner(session: Session, username: str) -> User | None:
    return session.scalar(select(User).where(func.lower(User.username) == username.casefold()))


def _check_password(password: str) -> None:
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")


def _clean_member_role(role: str) -> str:
    key = role.strip().casefold().replace(" ", "_")
    if key in {"super_admin", "client_admin", "superadmin", "admin"}:
        raise HTTPException(status_code=400, detail="That role is reserved for an admin account.")
    if key in PRESET_ROLES:
        return key
    if len(role.strip()) < 2 or len(role.strip()) > 40:
        raise HTTPException(status_code=400, detail="Enter a role such as Quality or Engineer.")
    return role.strip()


@app.get("/")
def index():
    return FileResponse(
        FRONTEND / "index.html",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/api/status")
def status():
    key, model, source = anthropic_config()
    info = database_info(database_url())
    return {
        "database": info,
        "anthropic": {
            "configured": bool(key),
            "source": source,
            "model": model,
            "hint": "" if key else "Paste your Anthropic API key in Settings.",
        },
    }


def _norm_device(value: str) -> str:
    return re.sub(r"[\s\-{}]", "", str(value or "")).upper()


def _host_device() -> dict:
    name = platform.node() or "This device"
    guid = ""
    if platform.system() == "Windows":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                guid, _kind = winreg.QueryValueEx(key, "MachineGuid")
        except OSError:
            guid = ""
    return {
        "device_id": str(guid or name).strip(),
        "serial": str(guid or "").strip(),
        "device_name": name,
        "model": platform.machine() or platform.system(),
        "platform": platform.system(),
    }


def _tablet_for_device(session: Session, body: DeviceIn) -> Tablet | None:
    detected = {_norm_device(body.device_id), _norm_device(body.serial)}
    detected.discard("")
    model = body.model.strip().casefold()
    tablets = session.scalars(select(Tablet)).all()
    for tablet in tablets:
        if detected and _norm_device(tablet.device_id) in detected:
            return tablet
        if tablet.device_id.startswith("MODEL") and tablet.model and model and tablet.model.casefold() == model:
            return tablet
    return None


def _license_block(session: Session, user: User, device: DeviceIn) -> str | None:
    if user.role == "super_admin" or not user.client_id:
        return None
    tablets = session.scalars(select(Tablet).where(Tablet.client_id == user.client_id)).all()
    if not tablets:
        return None
    approved = [tablet for tablet in tablets if tablet.status == "approved"]
    found = _tablet_for_device(session, device)
    if found and found.client_id == user.client_id and found.status == "approved":
        return None
    if found and found.client_id == user.client_id and found.status == "pending":
        return "This tablet matches. Waiting for the super admin to approve it."
    # WebView calls GET /api/device on the server and may send the host id instead of the tablet serial.
    if len(approved) == 1 and (found is None or found.client_id != user.client_id):
        host_id = _norm_device(_host_device()["device_id"])
        sent = {_norm_device(device.device_id), _norm_device(device.serial)}
        sent.discard("")
        if sent & {host_id, _norm_device(approved[0].device_id)}:
            return None
    return "This tablet is not approved. The super admin has to approve it before this login can be used."


@app.get("/api/device")
def device_identity():
    return _host_device()


@app.post("/api/license/check")
def license_check(body: DeviceIn):
    if not _norm_device(body.device_id) and not _norm_device(body.serial) and not body.model.strip():
        raise HTTPException(status_code=400, detail="This device has no details.")
    with db_session() as session:
        tablet = _tablet_for_device(session, body)
        if tablet is None:
            return {"matched": False, "status": "", "message": "This device is not registered yet. Sign in if you are the super admin."}
        if tablet.status == "registered":
            tablet.status = "pending"
            tablet.detected_name = body.device_name or tablet.detected_name
            tablet.detected_platform = body.platform or tablet.detected_platform
            tablet.seen_at = utcnow()
            session.commit()
        client = session.get(Client, tablet.client_id)
        messages = {
            "pending": "This tablet matches the registered details. Waiting for the super admin to approve it.",
            "approved": "This tablet is approved. Sign in with your username and password.",
        }
        return {
            "matched": True,
            "status": tablet.status,
            "tablet_name": tablet.name,
            "client_name": client.name if client else "",
            "message": messages.get(tablet.status, "This tablet is registered."),
        }


@app.post("/api/login")
def login(body: LoginIn, response: Response):
    username = body.username.strip()
    if not username or not body.password:
        raise HTTPException(status_code=400, detail="Enter the username and password.")
    with db_session() as session:
        user = _username_owner(session, username)
        if not user or not user.active or not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Username or password is wrong.")
        blocked = _license_block(session, user, DeviceIn(device_id=body.device_id, model=body.model, serial=body.serial))
        if blocked:
            raise HTTPException(status_code=403, detail=blocked)
        client_name = ""
        if user.client_id:
            client = session.get(Client, user.client_id)
            client_name = client.name if client else ""
        auth = AuthSession(token=secrets.token_urlsafe(32), user_id=user.id, expires_at=session_expiry())
        session.add(auth)
        session.commit()
        public = _public_user(user, client_name)
        token = auth.token
    response.set_cookie("ie_session", token, httponly=True, samesite="lax", max_age=14 * 24 * 3600, path="/")
    return public


@app.post("/api/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("ie_session", "")
    if token:
        with db_session() as session:
            row = session.scalar(select(AuthSession).where(AuthSession.token == token))
            if row:
                session.delete(row)
                session.commit()
    response.delete_cookie("ie_session", path="/")
    return {"ok": True}


@app.get("/api/me")
def me():
    return actor()


@app.get("/api/clients")
def list_clients():
    _require_super()
    with db_session() as session:
        clients = session.scalars(select(Client).order_by(Client.name)).all()
        payload = []
        for client in clients:
            users = session.scalars(select(User).where(User.client_id == client.id)).all()
            admin = next((item for item in users if item.role == "client_admin"), None)
            payload.append(
                {
                    "id": client.id,
                    "name": client.name,
                    "admin_username": admin.username if admin else "",
                    "admin_name": admin.display_name if admin else "",
                    "user_count": len(users),
                }
            )
        return payload


@app.post("/api/clients")
def create_client(body: ClientIn):
    _require_super()
    if not body.name:
        raise HTTPException(status_code=400, detail="Client name is required.")
    if not body.admin_username:
        raise HTTPException(status_code=400, detail="Admin username is required.")
    _check_password(body.admin_password)
    with db_session() as session:
        if _username_owner(session, body.admin_username):
            raise HTTPException(status_code=400, detail="That username is already in use.")
        client = Client(name=body.name)
        session.add(client)
        session.flush()
        session.add(
            User(
                client_id=client.id,
                username=body.admin_username,
                password_hash=hash_password(body.admin_password),
                display_name=body.admin_name or body.name,
                role="client_admin",
            )
        )
        if body.device_name or body.device_model or body.device_serial:
            serial = _norm_device(body.device_serial)
            if len(serial) < 4 and len(body.device_model.strip()) < 2:
                raise HTTPException(status_code=400, detail="Enter the device model or the serial number.")
            device_id = serial or f"MODEL{client.id}{_norm_device(body.device_model)}"
            if session.scalar(select(Tablet).where(Tablet.device_id == device_id)):
                raise HTTPException(status_code=400, detail="That device is already registered.")
            session.add(
                Tablet(
                    client_id=client.id,
                    name=body.device_name or f"{body.name} tablet",
                    model=body.device_model,
                    device_id=device_id,
                    status="registered",
                )
            )
        session.commit()
        session.refresh(client)
        return {"id": client.id, "name": client.name}


def _tablet_dict(tablet: Tablet, client_name: str) -> dict:
    return {
        "id": tablet.id,
        "client_id": tablet.client_id,
        "client_name": client_name,
        "name": tablet.name,
        "model": tablet.model,
        "device_id": tablet.device_id,
        "detected_name": tablet.detected_name,
        "detected_platform": tablet.detected_platform,
        "status": tablet.status,
        "seen_at": tablet.seen_at.isoformat() if tablet.seen_at else None,
    }


@app.get("/api/tablets")
def list_tablets():
    _require_super()
    with db_session() as session:
        tablets = session.scalars(select(Tablet).order_by(Tablet.created_at.desc())).all()
        payload = []
        for tablet in tablets:
            client = session.get(Client, tablet.client_id)
            payload.append(_tablet_dict(tablet, client.name if client else ""))
        return payload


@app.post("/api/tablets")
def create_tablet(body: TabletIn):
    _require_super()
    if not body.name:
        raise HTTPException(status_code=400, detail="Device name is required.")
    serial = _norm_device(body.device_id)
    if len(serial) < 4 and len(body.model.strip()) < 2:
        raise HTTPException(status_code=400, detail="Enter the model or the serial number.")
    device_id = serial or f"MODEL{body.client_id}{_norm_device(body.model)}"
    with db_session() as session:
        client = session.get(Client, body.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found.")
        if session.scalar(select(Tablet).where(Tablet.device_id == device_id)):
            raise HTTPException(status_code=400, detail="That device is already registered.")
        tablet = Tablet(client_id=client.id, name=body.name, model=body.model, device_id=device_id, status="registered")
        session.add(tablet)
        session.commit()
        session.refresh(tablet)
        return _tablet_dict(tablet, client.name)


@app.post("/api/tablets/{tablet_id}/approve")
def approve_tablet(tablet_id: int):
    _require_super()
    with db_session() as session:
        tablet = session.get(Tablet, tablet_id)
        if not tablet:
            raise HTTPException(status_code=404, detail="Tablet not found.")
        if tablet.status == "approved":
            raise HTTPException(status_code=400, detail="This tablet is already approved.")
        tablet.status = "approved"
        session.commit()
        client = session.get(Client, tablet.client_id)
        return _tablet_dict(tablet, client.name if client else "")


@app.delete("/api/tablets/{tablet_id}")
def delete_tablet(tablet_id: int):
    _require_super()
    with db_session() as session:
        tablet = session.get(Tablet, tablet_id)
        if not tablet:
            raise HTTPException(status_code=404, detail="Tablet not found.")
        session.delete(tablet)
        session.commit()
    return {"ok": True}


@app.delete("/api/clients/{client_id}")
def delete_client(client_id: int):
    _require_super()
    with db_session() as session:
        client = session.get(Client, client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found.")
        jobs = session.scalars(select(Job).where(Job.client_id == client_id)).all()
        for job in jobs:
            session.delete(job)
        for tablet in session.scalars(select(Tablet).where(Tablet.client_id == client_id)).all():
            session.delete(tablet)
        session.delete(client)
        session.commit()
    return {"ok": True}


@app.get("/api/users")
def list_users():
    user = _require_client_admin()
    with db_session() as session:
        rows = session.scalars(select(User).where(User.client_id == user["client_id"]).order_by(User.display_name, User.username)).all()
        return [
            {
                "id": item.id,
                "username": item.username,
                "display_name": item.display_name,
                "role": item.role,
                "role_label": role_label(item.role),
            }
            for item in rows
            if item.role != "client_admin"
        ]


@app.post("/api/users")
def create_user(body: UserIn):
    user = _require_client_admin()
    if not body.username:
        raise HTTPException(status_code=400, detail="Username is required.")
    _check_password(body.password)
    role = _clean_member_role(body.role)
    with db_session() as session:
        if _username_owner(session, body.username):
            raise HTTPException(status_code=400, detail="That username is already in use.")
        created = User(
            client_id=user["client_id"],
            username=body.username,
            password_hash=hash_password(body.password),
            display_name=body.display_name or body.username,
            role=role,
        )
        session.add(created)
        session.commit()
        session.refresh(created)
        return {
            "id": created.id,
            "username": created.username,
            "display_name": created.display_name,
            "role": created.role,
            "role_label": role_label(created.role),
        }


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int):
    user = _require_client_admin()
    with db_session() as session:
        target = session.get(User, user_id)
        if not target or target.client_id != user["client_id"] or target.role == "client_admin":
            raise HTTPException(status_code=404, detail="User not found.")
        session.delete(target)
        session.commit()
    return {"ok": True}


@app.post("/api/settings")
def update_settings(body: SettingsIn):
    _require_super()
    payload = {}
    if body.anthropic_model.strip():
        payload["anthropic_model"] = body.anthropic_model.strip()
    if body.anthropic_api_key.strip():
        payload["anthropic_api_key"] = body.anthropic_api_key.strip()
    if payload:
        save_settings(payload)
    key, model, source = anthropic_config()
    return {"configured": bool(key), "source": source, "model": model}


def instrument_dict(item: Instrument) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "asset_id": item.asset_id,
        "range_min": item.range_min,
        "range_max": item.range_max,
        "unit": item.unit,
    }


def row_dict(row: BalloonRow | dict, instruments: list[dict]) -> dict:
    data = row if isinstance(row, dict) else {
        "id": row.id,
        "page_no": row.page_no,
        "work_area": row.work_area,
        "balloon_no": row.balloon_no,
        "characteristic": row.characteristic,
        "nominal": row.nominal,
        "tol_plus": row.tol_plus,
        "tol_minus": row.tol_minus,
        "unit": row.unit,
        "instrument": row.instrument,
        "instrument_id": row.instrument_id,
        "m1": row.m1,
        "m2": row.m2,
        "m1_confident": row.m1_confident,
        "m2_confident": row.m2_confident,
        "m1_source": row.m1_source,
        "m2_source": row.m2_source,
        "sheet_result": row.sheet_result,
        "ocr_note": row.ocr_note,
        "sort_index": row.sort_index,
    }
    verdict = evaluate(data, instruments)
    data = dict(data)
    data.update(
        {
            "result": verdict.result,
            "range_warning": verdict.range_warning,
            "id_mismatch": verdict.id_mismatch,
            "reasons": verdict.reasons,
            "lsl": verdict.lsl,
            "usl": verdict.usl,
            "m1_status": verdict.m1_status,
            "m2_status": verdict.m2_status,
        }
    )
    return data


def job_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "job_number": job.job_number,
        "part_number": job.part_number,
        "part_name": job.part_name,
        "revision": job.revision,
        "drawing_number": job.drawing_number,
        "customer": job.customer,
        "work_order": job.work_order,
        "notes": job.notes,
        "mass": job.mass,
        "report_date": job.report_date,
        "company_name": job.company_name,
        "material": job.material,
        "finish_treatment": job.finish_treatment,
        "serial_number": job.serial_number,
        "supplier_name": job.supplier_name,
        "authorized_signatory": job.authorized_signatory,
        "doc_number": job.doc_number,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def load_job(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    user = actor()
    if not job or (user["role"] != "super_admin" and job.client_id != user["client_id"]):
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


def instrument_list(session: Session) -> list[dict]:
    items = session.scalars(select(Instrument).order_by(Instrument.name, Instrument.asset_id)).all()
    return [instrument_dict(item) for item in items]


@app.get("/api/instruments")
def list_instruments():
    with db_session() as session:
        return instrument_list(session)


@app.post("/api/instruments")
def add_instrument(body: InstrumentIn):
    if not body.name:
        raise HTTPException(status_code=400, detail="Instrument name is required.")
    if body.range_min is not None and body.range_max is not None and body.range_min > body.range_max:
        raise HTTPException(status_code=400, detail="Range minimum is above the maximum.")
    with db_session() as session:
        item = Instrument(
            name=body.name,
            asset_id=body.asset_id,
            range_min=body.range_min,
            range_max=body.range_max,
            unit=body.unit,
        )
        session.add(item)
        session.commit()
        return instrument_dict(item)


@app.delete("/api/instruments/{instrument_id}")
def delete_instrument(instrument_id: int):
    with db_session() as session:
        item = session.get(Instrument, instrument_id)
        if not item:
            raise HTTPException(status_code=404, detail="Instrument not found.")
        session.delete(item)
        session.commit()
    return {"ok": True}


@app.get("/api/jobs")
def list_jobs():
    user = actor()
    with db_session() as session:
        query = select(Job).order_by(Job.updated_at.desc())
        if user["role"] == "super_admin":
            query = query.where(Job.client_id.is_(None))
        else:
            query = query.where(Job.client_id == user["client_id"])
        jobs = session.scalars(query).all()
        payload = []
        for job in jobs:
            item = job_dict(job)
            item["row_count"] = len(job.rows)
            payload.append(item)
        return payload


@app.post("/api/jobs")
def create_job(body: JobIn):
    with db_session() as session:
        number = body.job_number or _next_number(session)
        if session.scalar(select(Job).where(Job.job_number == number)):
            raise HTTPException(status_code=400, detail=f"Job {number} already exists.")
        job = Job(job_number=number, client_id=actor()["client_id"])
        _apply_job(job, body)
        job.job_number = number
        session.add(job)
        session.commit()
        session.refresh(job)
        return job_dict(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    with db_session() as session:
        job = load_job(session, job_id)
        instruments = instrument_list(session)
        return {
            "job": job_dict(job),
            "rows": [row_dict(row, instruments) for row in job.rows],
            "instruments": instruments,
        }


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int):
    with db_session() as session:
        job = load_job(session, job_id)
        session.delete(job)
        session.commit()
    return {"ok": True}


@app.put("/api/jobs/{job_id}")
def save_job(job_id: int, body: SaveIn):
    with db_session() as session:
        job = load_job(session, job_id)
        _write_job(session, job, body)
        session.commit()
        session.refresh(job)
        instruments = instrument_list(session)
        return {"job": job_dict(job), "rows": [row_dict(row, instruments) for row in job.rows]}


@app.post("/api/jobs/{job_id}/check")
def check_job(job_id: int, body: SaveIn):
    with db_session() as session:
        load_job(session, job_id)
        instruments = instrument_list(session)
        rows = [row_dict(item.model_dump(), instruments) for item in body.rows]
        counts = _counts(rows)
        return {"rows": rows, "counts": counts}


@app.post("/api/jobs/{job_id}/upload")
async def upload_to_job(
    job_id: int,
    file: UploadFile = File(...),
    purpose: str = Form("report"),
    replace: str = Form("false"),
):
    payload = await _read_upload(file)
    with db_session() as session:
        job = load_job(session, job_id)
        return _run_upload(session, job, payload, file.filename or "upload", purpose, _flag(replace))


@app.post("/api/upload")
async def upload_new_job(
    file: UploadFile = File(...),
    purpose: str = Form("report"),
    replace: str = Form("false"),
):
    payload = await _read_upload(file)
    with db_session() as session:
        number = _unique_number(session, Path(file.filename or "report").stem)
        job = Job(job_number=number, part_name=Path(file.filename or "").stem, client_id=actor()["client_id"])
        session.add(job)
        session.flush()
        return _run_upload(session, job, payload, file.filename or "upload", purpose, _flag(replace))


def _run_upload(session: Session, job: Job, payload: bytes, filename: str, purpose: str, replace: bool):
    if purpose not in {"report", "filled", "plan"}:
        raise HTTPException(status_code=400, detail="Unknown upload purpose.")
    instruments = instrument_list(session)
    existing = [row_dict(row, instruments) for row in job.rows]
    try:
        read = read_document(payload, filename, existing, purpose)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    merged, summary = apply_extraction(existing, read["rows"], purpose, replace)
    summary["warnings"] = read["warnings"] + summary.get("warnings", [])
    summary["pages"] = read["pages"]
    summary["ocr_preview"] = read["ocr_preview"]
    _apply_meta(job, read["meta"], filename)
    _replace_rows(session, job, merged)
    stored = _store_upload(job, filename, payload)
    upload = Upload(
        job_id=job.id,
        filename=Path(filename).name,
        purpose=purpose,
        stored_path=str(stored),
        summary={key: value for key, value in summary.items() if key != "ocr_preview"},
    )
    session.add(upload)
    job.updated_at = utcnow()
    session.commit()
    session.refresh(job)
    instruments = instrument_list(session)
    rows = [row_dict(row, instruments) for row in job.rows]
    summary.update(_count_lists(rows))
    return {
        "job": job_dict(job),
        "rows": rows,
        "summary": summary,
        "upload_id": upload.id,
    }


@app.get("/api/uploads/{upload_id}/file")
def download_upload(upload_id: int):
    with db_session() as session:
        upload = session.get(Upload, upload_id)
        if not upload:
            raise HTTPException(status_code=404, detail="Upload not found.")
        path = Path(upload.stored_path)
        if not path.is_file() or UPLOADS.resolve() not in path.resolve().parents:
            raise HTTPException(status_code=404, detail="Stored file is missing.")
        return FileResponse(path, filename=upload.filename)


def _export_response(kind: str, job_id: int, scope: str, page: int | None, work_area: str | None) -> Response:
    job, rows, instruments = _scoped(job_id, scope, page, work_area)
    stem = _file_stem(job, scope)
    if kind == "export.csv":
        return Response(
            build_csv(job, rows, instruments),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{stem}-inspection.csv"'},
        )
    if kind == "export.xlsx":
        return Response(
            build_xlsx(job, rows, instruments),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{stem}-inspection.xlsx"'},
        )
    if kind == "export.pdf":
        content = build_pdf(job, rows, instruments, blank_measurements=False, seal_path=_job_seal(job_id))
        return Response(
            content,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{stem}-inspection.pdf"'},
        )
    if kind == "blank.pdf":
        content = build_pdf(job, rows, instruments, blank_measurements=False, seal_path=_job_seal(job_id))
        return Response(
            content,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{stem}-report.pdf"'},
        )
    raise HTTPException(status_code=400, detail="Unknown export type.")


@app.post("/api/jobs/{job_id}/export-link")
def create_export_link(job_id: int, body: ExportLinkIn):
    load_user = actor()
    if body.kind not in EXPORT_KINDS:
        raise HTTPException(status_code=400, detail="Unknown export type.")
    if body.scope not in {"job", "page", "area"}:
        raise HTTPException(status_code=400, detail="Scope must be job, page, or area.")
    with db_session() as session:
        load_job(session, job_id)
    _prune_export_links()
    token = secrets.token_urlsafe(32)
    _export_links[token] = {
        "job_id": job_id,
        "kind": body.kind,
        "scope": body.scope,
        "page": body.page,
        "work_area": body.work_area or None,
        "expires": time.time() + EXPORT_LINK_TTL,
        "user_id": load_user["id"],
    }
    return {"path": f"/api/export/{token}", "expires_in": EXPORT_LINK_TTL}


@app.get("/api/export/{token}")
def export_by_token(token: str):
    _prune_export_links()
    entry = _export_links.pop(token, None)
    if not entry or entry["expires"] < time.time():
        raise HTTPException(status_code=404, detail="This download link expired. Tap Save again.")
    export_user = _actor_for_user_id(entry["user_id"])
    token_ctx = _actor.set(export_user)
    try:
        return _export_response(
            entry["kind"],
            entry["job_id"],
            entry["scope"],
            entry.get("page"),
            entry.get("work_area"),
        )
    finally:
        _actor.reset(token_ctx)


@app.get("/api/jobs/{job_id}/export.csv")
def export_csv(job_id: int, scope: str = "job", page: int | None = None, work_area: str | None = None):
    return _export_response("export.csv", job_id, scope, page, work_area)


@app.get("/api/jobs/{job_id}/export.xlsx")
def export_xlsx(job_id: int, scope: str = "job", page: int | None = None, work_area: str | None = None):
    return _export_response("export.xlsx", job_id, scope, page, work_area)


@app.get("/api/jobs/{job_id}/export.pdf")
def export_pdf(job_id: int, scope: str = "job", page: int | None = None, work_area: str | None = None):
    return _export_response("export.pdf", job_id, scope, page, work_area)


@app.get("/api/jobs/{job_id}/blank.pdf")
def blank_pdf(job_id: int, scope: str = "job", page: int | None = None, work_area: str | None = None):
    return _export_response("blank.pdf", job_id, scope, page, work_area)


def _job_seal(job_id: int) -> str | None:
    dest = UPLOADS / f"{job_id}-seal.png"
    with db_session() as session:
        upload = session.scalar(select(Upload).where(Upload.job_id == job_id).order_by(Upload.created_at.desc()))
        if not upload:
            return None
        source = Path(upload.stored_path)
        if not source.is_file():
            return None
        if dest.is_file() and dest.stat().st_mtime >= source.stat().st_mtime:
            return str(dest)
        if save_seal(source, dest):
            return str(dest)
        if dest.is_file():
            dest.unlink()
        return None


def _scoped(job_id: int, scope: str, page: int | None, work_area: str | None):
    if scope not in {"job", "page", "area"}:
        raise HTTPException(status_code=400, detail="Scope must be job, page, or area.")
    with db_session() as session:
        job = load_job(session, job_id)
        instruments = instrument_list(session)
        rows = [row_dict(row, instruments) for row in job.rows]
        chosen = filter_rows(rows, scope, page, work_area)
        if not chosen:
            raise HTTPException(status_code=400, detail="Nothing in that page or work area to export.")
        return job_dict(job), chosen, instruments


def _counts(rows: list[dict]) -> dict:
    counts = {"pass": 0, "fail": 0, "review": 0, "incomplete": 0, "empty": 0, "range": 0}
    for row in rows:
        counts[row["result"]] = counts.get(row["result"], 0) + 1
        if row.get("range_warning"):
            counts["range"] += 1
    return counts


def _count_lists(rows: list[dict]) -> dict:
    return {
        "out_of_tolerance": [
            f"{row['balloon_no']} {'; '.join(row['reasons'])}" for row in rows if row["result"] == "fail"
        ],
        "needs_review": [
            f"{row['balloon_no']} {'; '.join(row['reasons'])}" for row in rows if row["result"] == "review"
        ],
        "outside_instrument_range": [
            str(row["balloon_no"]) for row in rows if row.get("range_warning")
        ],
        "counts": _counts(rows),
    }


def _apply_job(job: Job, body: JobIn) -> None:
    for field in (
        "part_number",
        "part_name",
        "revision",
        "drawing_number",
        "customer",
        "work_order",
        "notes",
        "mass",
        "report_date",
        "company_name",
        "material",
        "finish_treatment",
        "serial_number",
        "supplier_name",
        "authorized_signatory",
        "doc_number",
    ):
        setattr(job, field, getattr(body, field))


def _write_job(session: Session, job: Job, body: SaveIn) -> None:
    number = body.job.job_number or job.job_number
    clash = session.scalar(select(Job).where(Job.job_number == number, Job.id != job.id))
    if clash:
        raise HTTPException(status_code=400, detail=f"Job {number} already exists.")
    job.job_number = number
    _apply_job(job, body.job)
    _replace_rows(session, job, [item.model_dump() for item in body.rows])
    job.updated_at = utcnow()


def _replace_rows(session: Session, job: Job, rows: list[dict]) -> None:
    kept = []
    existing = {row.id: row for row in job.rows}
    for index, item in enumerate(rows):
        row_id = item.get("id")
        if row_id and row_id in existing:
            row = existing[row_id]
        else:
            row = BalloonRow(job_id=job.id)
            session.add(row)
        _apply_row(row, item, index)
        kept.append(row)
    session.flush()
    keep_ids = {row.id for row in kept if row.id}
    for row in list(job.rows):
        if row.id not in keep_ids:
            session.delete(row)


def _apply_row(row: BalloonRow, item: dict, index: int) -> None:
    parsed = RowIn.model_validate(item)
    for field in (
        "page_no",
        "work_area",
        "balloon_no",
        "characteristic",
        "nominal",
        "tol_plus",
        "tol_minus",
        "unit",
        "instrument",
        "instrument_id",
        "m1",
        "m2",
        "m1_confident",
        "m2_confident",
        "m1_source",
        "m2_source",
        "sheet_result",
        "ocr_note",
    ):
        setattr(row, field, getattr(parsed, field))
    row.sort_index = index
    row.updated_at = utcnow()


def _apply_meta(job: Job, meta: dict, filename: str = "") -> None:
    mapping = {
        "part_number": "part_number",
        "part_name": "part_name",
        "revision": "revision",
        "drawing_number": "drawing_number",
        "customer": "customer",
        "report_date": "report_date",
        "serial_number": "serial_number",
        "company_name": "company_name",
        "material": "material",
        "finish_treatment": "finish_treatment",
        "supplier_name": "supplier_name",
        "authorized_signatory": "authorized_signatory",
        "doc_number": "doc_number",
        "mass": "mass",
    }
    stem = Path(filename).stem.strip()
    stem_spaced = stem.replace("_", " ")
    for source, target in mapping.items():
        value = str(meta.get(source) or "").strip()
        if not value:
            continue
        current = str(getattr(job, target) or "").strip()
        if not current or current in {stem, stem_spaced}:
            setattr(job, target, value[:200])


def _store_upload(job: Job, filename: str, payload: bytes) -> Path:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)[:80] or "upload"
    path = UPLOADS / f"{job.id}-{uuid4().hex}-{safe}"
    path.write_bytes(payload)
    return path


async def _read_upload(file: UploadFile) -> bytes:
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(payload) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Keep the upload under 20 MB.")
    return payload


def _next_number(session: Session) -> str:
    count = session.query(Job).count() + 1
    while session.scalar(select(Job).where(Job.job_number == f"IE-{count:04d}")):
        count += 1
    return f"IE-{count:04d}"


def _unique_number(session: Session, stem: str) -> str:
    base = re.sub(r"[^A-Za-z0-9-]+", "-", stem).strip("-")[:40] or _next_number(session)
    number = base
    suffix = 2
    while session.scalar(select(Job).where(Job.job_number == number)):
        number = f"{base}-{suffix}"
        suffix += 1
    return number


def _flag(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _file_stem(job: dict, scope: str) -> str:
    number = re.sub(r"[^A-Za-z0-9-]+", "-", job.get("job_number") or "job")
    return f"{number}-{scope}"
