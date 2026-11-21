from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth import SUPERADMIN_PASSWORD, SUPERADMIN_USERNAME
from app.main import app

client = TestClient(app)


def _login(username=SUPERADMIN_USERNAME, password=SUPERADMIN_PASSWORD):
    response = client.post("/api/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def test_save_keeps_out_of_range_instrument_editable_value():
    _login()
    number = f"RANGE-{uuid4().hex[:8]}"
    created = client.post("/api/jobs", json={"job_number": number, "part_number": "P-1", "part_name": "Pin"}).json()
    saved = client.put(
        f"/api/jobs/{created['id']}",
        json={
            "job": {"job_number": number, "part_number": "P-1", "part_name": "Pin"},
            "rows": [
                {
                    "balloon_no": "1",
                    "page_no": 1,
                    "work_area": "Mill",
                    "characteristic": "Length",
                    "nominal": "120",
                    "tol_plus": 0.2,
                    "tol_minus": 0.1,
                    "unit": "mm",
                    "instrument": "Micrometer 0-25",
                    "instrument_id": "MIC-006",
                    "m1": "120.05",
                    "m2": "120.04",
                    "sheet_result": "ok",
                    "m1_source": "manual",
                    "m2_source": "manual",
                }
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    row = saved.json()["rows"][0]
    assert row["instrument"] == "Micrometer 0-25"
    assert row["instrument_id"] == "MIC-006"
    assert row["m1"] == "120.05"
    assert row["sheet_result"] == "ok"
    assert row["result"] == "pass"
    assert row["range_warning"] is True

    exported = client.get(f"/api/jobs/{created['id']}/export.csv", params={"scope": "page", "page": 1})
    assert exported.status_code == 200
    assert b"120.05" in exported.content
    assert b"Micrometer 0-25" in exported.content

    blank = client.get(f"/api/jobs/{created['id']}/blank.pdf")
    assert blank.status_code == 200
    assert blank.content.startswith(b"%PDF")
    import pymupdf

    report = pymupdf.open(stream=blank.content, filetype="pdf")
    text = "\n".join(page.get_text() for page in report)
    assert "Actual (T/W)" in text
    assert "Actual (F/W)" in text
    assert "Result" in text
    assert "Status" in text
    assert "ok" in text
    assert "120.05" in text
    assert "120.04" in text
    assert "In range" in text

    client.delete(f"/api/jobs/{created['id']}")
