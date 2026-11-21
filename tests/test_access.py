from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth import SUPERADMIN_PASSWORD, SUPERADMIN_USERNAME
from app.main import app


def test_report_stays_behind_sign_in_and_clients_stay_separate():
    guest = TestClient(app)
    assert guest.get("/api/jobs").status_code == 401

    owner = TestClient(app)
    signed = owner.post("/api/login", json={"username": SUPERADMIN_USERNAME, "password": SUPERADMIN_PASSWORD})
    assert signed.status_code == 200
    assert signed.json()["role"] == "super_admin"

    suffix = uuid4().hex[:8]
    created = owner.post(
        "/api/clients",
        json={
            "name": f"Plant {suffix}",
            "admin_name": "Plant Admin",
            "admin_username": f"admin{suffix}",
            "admin_password": "Plant-2026",
        },
    )
    assert created.status_code == 200, created.text

    admin = TestClient(app)
    admin_login = admin.post("/api/login", json={"username": f"admin{suffix}", "password": "Plant-2026"})
    assert admin_login.status_code == 200
    assert admin_login.json()["role"] == "client_admin"

    added = admin.post(
        "/api/users",
        json={"display_name": "Asha", "username": f"quality{suffix}", "password": "Quality-2026", "role": "quality"},
    )
    assert added.status_code == 200, added.text
    assert added.json()["role_label"] == "Quality"

    job = admin.post("/api/jobs", json={"job_number": f"JOB-{suffix}", "part_name": "Chassis"}).json()
    assert admin.get(f"/api/jobs/{job['id']}").status_code == 200

    other = TestClient(app)
    other.post("/api/login", json={"username": SUPERADMIN_USERNAME, "password": SUPERADMIN_PASSWORD})
    assert other.get(f"/api/jobs/{job['id']}").status_code == 200
    listed = [item["job_number"] for item in other.get("/api/jobs").json()]
    assert f"JOB-{suffix}" not in listed

    quality = TestClient(app)
    quality_login = quality.post("/api/login", json={"username": f"quality{suffix}", "password": "Quality-2026"})
    assert quality_login.json()["role"] == "quality"
    assert quality.get("/api/users").status_code == 403
    assert quality.get(f"/api/jobs/{job['id']}").status_code == 200

    owner.delete(f"/api/clients/{created.json()['id']}")


def test_tablet_must_match_and_be_approved_before_client_login():
    owner = TestClient(app)
    owner.post("/api/login", json={"username": SUPERADMIN_USERNAME, "password": SUPERADMIN_PASSWORD})
    suffix = uuid4().hex[:8]
    created = owner.post(
        "/api/clients",
        json={
            "name": f"Shop {suffix}",
            "admin_name": "Shop Admin",
            "admin_username": f"shop{suffix}",
            "admin_password": "Shop-2026",
        },
    )
    assert created.status_code == 200, created.text
    client_id = created.json()["id"]
    device = "TAB-DEVICE-44"
    added = owner.post("/api/tablets", json={"client_id": client_id, "name": "Floor tablet", "device_id": device})
    assert added.status_code == 200, added.text
    assert added.json()["status"] == "registered"

    blocked = TestClient(app)
    denied = blocked.post("/api/login", json={"username": f"shop{suffix}", "password": "Shop-2026", "device_id": device})
    assert denied.status_code == 403

    check = blocked.post("/api/license/check", json={"device_id": device, "device_name": "Floor-1", "platform": "Windows"})
    assert check.status_code == 200
    assert check.json()["status"] == "pending"

    still = blocked.post("/api/login", json={"username": f"shop{suffix}", "password": "Shop-2026", "device_id": device})
    assert still.status_code == 403

    approved = owner.post(f"/api/tablets/{added.json()['id']}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    allowed = blocked.post("/api/login", json={"username": f"shop{suffix}", "password": "Shop-2026", "device_id": device})
    assert allowed.status_code == 200
    assert allowed.json()["role"] == "client_admin"

    other_device = TestClient(app)
    wrong = other_device.post("/api/login", json={"username": f"shop{suffix}", "password": "Shop-2026", "device_id": "OTHER-TABLET"})
    assert wrong.status_code == 403

    owner.delete(f"/api/clients/{client_id}")
