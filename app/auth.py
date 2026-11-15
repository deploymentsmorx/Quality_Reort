from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

SUPERADMIN_USERNAME = "superadmin"
SUPERADMIN_PASSWORD = "IE-Admin-2026"
SESSION_DAYS = 14

PRESET_ROLES = ("quality", "engineer", "production", "operation", "process")
ROLE_LABELS = {
    "super_admin": "Super admin",
    "client_admin": "Admin",
    "quality": "Quality",
    "engineer": "Engineer",
    "production": "Production",
    "operation": "Operation",
    "process": "Process",
}


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 120_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), 120_000).hex()
    return secrets.compare_digest(check, digest)


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def session_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)


def aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
