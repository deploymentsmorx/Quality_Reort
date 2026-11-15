from __future__ import annotations

from sqlalchemy.orm import Session

from app.auth import SUPERADMIN_PASSWORD, SUPERADMIN_USERNAME, hash_password
from app.models import BalloonRow, Instrument, Job, User

INSTRUMENTS = [
    ("Digital caliper 0-150", "CAL-014", 0, 150, "mm"),
    ("Digital caliper 0-150", "CAL-022", 0, 150, "mm"),
    ("Digital caliper 0-300", "CAL-301", 0, 300, "mm"),
    ("Micrometer 0-25", "MIC-006", 0, 25, "mm"),
    ("Micrometer 25-50", "MIC-019", 25, 50, "mm"),
    ("Height gauge 0-600", "HGT-003", 0, 600, "mm"),
    ("Bore gauge", "BRG-011", 6, 50, "mm"),
    ("Dial indicator", "IND-002", 0, 10, "mm"),
    ("CMM", "CMM-01", None, None, "mm"),
    ("Pin gauge set", "PIN-SET", None, None, None),
    ("Thread plug M6", "TP-M6", None, None, None),
    ("Visual", "VIS", None, None, None),
    ("MT", "", None, None, "mm"),
    ("TPG", "", None, None, ""),
    ("BP", "", None, None, "deg"),
]

SAMPLE_ROWS = [
    (1, "Mill", "1", "Overall length", "120.00", 0.20, 0.10, "mm"),
    (1, "Mill", "2", "Hole diameter", "10.00", 0.05, 0.00, "mm"),
    (1, "Mill", "3", "Thickness", "6.00", 0.10, 0.10, "mm"),
    (1, "Mill", "4", "Edge distance", "18.00", 0.15, 0.15, "mm"),
    (2, "Final inspect", "5", "Slot width", "8.00", 0.10, 0.05, "mm"),
    (2, "Final inspect", "6", "Flatness", "0.05", 0.00, 0.05, "mm"),
    (2, "Final inspect", "7", "Chamfer", "1.00", 0.30, 0.30, "mm"),
    (2, "Final inspect", "8", "Thread M6", "M6x1", None, None, ""),
]


def seed(session: Session) -> None:
    if session.query(Instrument).count() == 0:
        for name, asset_id, low, high, unit in INSTRUMENTS:
            session.add(
                Instrument(
                    name=name,
                    asset_id=asset_id,
                    range_min=low,
                    range_max=high,
                    unit=unit or "",
                )
            )
    for name, asset_id, low, high, unit in INSTRUMENTS:
        if name in {"MT", "TPG", "BP"} and session.query(Instrument).filter_by(name=name).count() == 0:
            session.add(
                Instrument(name=name, asset_id=asset_id, range_min=low, range_max=high, unit=unit or "")
            )
    if session.query(Job).count() == 0:
        job = Job(
            job_number="SAMPLE-2407",
            part_number="BRK-2407",
            part_name="Mounting bracket",
            revision="C",
            drawing_number="BRK-2407-C",
            customer="Shop sample",
            work_order="WO-2407",
            notes="Sample balloon rows. Type M1/M2, or print the sheet and upload a photo of the table.",
        )
        session.add(job)
        session.flush()
        for index, item in enumerate(SAMPLE_ROWS):
            page, area, balloon, characteristic, nominal, plus, minus, unit = item
            session.add(
                BalloonRow(
                    job_id=job.id,
                    page_no=page,
                    work_area=area,
                    balloon_no=balloon,
                    characteristic=characteristic,
                    nominal=nominal,
                    tol_plus=plus,
                    tol_minus=minus,
                    unit=unit,
                    sort_index=index,
                )
            )
    if session.query(User).filter_by(role="super_admin").count() == 0:
        session.add(
            User(
                username=SUPERADMIN_USERNAME,
                password_hash=hash_password(SUPERADMIN_PASSWORD),
                display_name="Super Admin",
                role="super_admin",
                client_id=None,
            )
        )
    session.commit()
