from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


class JobIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_number: str = ""
    part_number: str = ""
    part_name: str = ""
    revision: str = ""
    drawing_number: str = ""
    customer: str = ""
    work_order: str = ""
    notes: str = ""
    mass: str = ""
    report_date: str = ""
    company_name: str = ""
    material: str = ""
    finish_treatment: str = ""
    serial_number: str = ""
    supplier_name: str = ""
    authorized_signatory: str = ""
    doc_number: str = ""

    @field_validator(
        "job_number",
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
        mode="before",
    )
    @classmethod
    def clean(cls, value):
        return _text(value)


class RowIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int | None = None
    page_no: int = 1
    work_area: str = ""
    balloon_no: str = ""
    characteristic: str = ""
    nominal: str = ""
    tol_plus: float | None = None
    tol_minus: float | None = None
    unit: str = "mm"
    instrument: str = ""
    instrument_id: str = ""
    m1: str = ""
    m2: str = ""
    m1_confident: bool = True
    m2_confident: bool = True
    m1_source: str = "manual"
    m2_source: str = "manual"
    sheet_result: str = ""
    ocr_note: str = ""
    sort_index: int = 0

    @field_validator(
        "work_area",
        "balloon_no",
        "characteristic",
        "nominal",
        "unit",
        "instrument",
        "instrument_id",
        "m1",
        "m2",
        "m1_source",
        "m2_source",
        "sheet_result",
        "ocr_note",
        mode="before",
    )
    @classmethod
    def clean(cls, value):
        return _text(value)

    @field_validator("tol_plus", "tol_minus", mode="before")
    @classmethod
    def clean_tol(cls, value):
        if value is None or str(value).strip() == "":
            return None
        text = str(value).strip().replace(",", ".").replace("±", "").replace("+", "").replace("−", "-")
        return abs(float(text))

    @field_validator("page_no", mode="before")
    @classmethod
    def clean_page(cls, value):
        try:
            page = int(value)
        except (TypeError, ValueError):
            return 1
        return page if page > 0 else 1


class SaveIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job: JobIn
    rows: list[RowIn]


class InstrumentIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    asset_id: str = ""
    range_min: float | None = None
    range_max: float | None = None
    unit: str = "mm"

    @field_validator("name", "asset_id", "unit", mode="before")
    @classmethod
    def clean(cls, value):
        return _text(value)

    @field_validator("range_min", "range_max", mode="before")
    @classmethod
    def clean_range(cls, value):
        if value is None or str(value).strip() == "":
            return None
        return float(str(value).strip())


class SettingsIn(BaseModel):
    anthropic_api_key: str = ""
    anthropic_model: str = ""


class ExportLinkIn(BaseModel):
    kind: str
    scope: str = "job"
    page: int | None = None
    work_area: str | None = None

    @field_validator("kind", "scope", "work_area", mode="before")
    @classmethod
    def clean_export_link(cls, value):
        return "" if value is None else str(value).strip()

    @field_validator("page", mode="before")
    @classmethod
    def clean_page(cls, value):
        if value is None or value == "":
            return None
        return int(value)


class LoginIn(BaseModel):
    username: str
    password: str
    device_id: str = ""
    model: str = ""
    serial: str = ""

    @field_validator("username", "password", "device_id", "model", "serial", mode="before")
    @classmethod
    def clean_login(cls, value):
        return "" if value is None else str(value).strip()


class ClientIn(BaseModel):
    name: str
    admin_name: str = ""
    admin_username: str
    admin_password: str
    device_name: str = ""
    device_model: str = ""
    device_serial: str = ""

    @field_validator("name", "admin_name", "admin_username", "device_name", "device_model", "device_serial", mode="before")
    @classmethod
    def clean_client(cls, value):
        return _text(value)


class TabletIn(BaseModel):
    client_id: int
    name: str
    model: str = ""
    device_id: str = ""

    @field_validator("name", "model", "device_id", mode="before")
    @classmethod
    def clean_tablet(cls, value):
        return _text(value)


class DeviceIn(BaseModel):
    device_id: str = ""
    device_name: str = ""
    platform: str = ""
    model: str = ""
    serial: str = ""

    @field_validator("device_id", "device_name", "platform", "model", "serial", mode="before")
    @classmethod
    def clean_device(cls, value):
        return _text(value)


class UserIn(BaseModel):
    display_name: str = ""
    username: str
    password: str
    role: str

    @field_validator("display_name", "username", "role", mode="before")
    @classmethod
    def clean_user(cls, value):
        return _text(value)
