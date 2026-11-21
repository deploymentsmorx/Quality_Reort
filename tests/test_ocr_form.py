from unittest.mock import patch

import io

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from app.ocr_pipeline import read_document


def _sheet() -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=landscape(A4))
    pdf.setFont("Helvetica-Bold", 16)
    headers = ["Balloon", "Nominal", "Instrument", "Instr. ID", "M1", "M2"]
    xs = [40, 160, 300, 480, 640, 750]
    pdf.drawString(40, 520, "Inspection table")
    for x, header in zip(xs, headers):
        pdf.drawString(x, 470, header)
    pdf.setFont("Helvetica", 16)
    rows = [
        ["1", "120.00", "Caliper", "CAL-014", "120.05", "120.08"],
        ["2", "10.00", "Micrometer", "MIC-006", "10.02", "10.01"],
    ]
    for index, row in enumerate(rows):
        y = 420 - index * 36
        for x, value in zip(xs, row):
            pdf.drawString(x, y, value)
    pdf.save()
    return buffer.getvalue()


def test_read_document_requires_api_key():
    try:
        read_document(_sheet(), "test.pdf", [], "report")
        assert False, "expected missing key"
    except ValueError as exc:
        assert "Anthropic API key" in str(exc)


def test_read_document_uses_claude_rows():
    sample_rows = [
        {
            "balloon_no": "1",
            "nominal": "120",
            "m1": "120.05",
            "m2": "120.08",
            "m1_confident": True,
            "m2_confident": True,
        }
    ]
    with patch("app.ocr_pipeline.anthropic_config", return_value=("test-key", "claude-sonnet-5-5", "env")):
        with patch(
            "app.ocr_pipeline.claude_read",
            return_value={"rows": sample_rows, "meta": {}, "warning": None},
        ):
            result = read_document(_sheet(), "test.pdf", [], "report")
    assert result["rows"]
    assert result["rows"][0]["balloon_no"] == "1"
    assert result["rows"][0]["m1"] == "120.05"