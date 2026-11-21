import io

from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from app.ocr_pipeline import tesseract_table


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


def test_tesseract_reads_printed_balloons_and_block_digits():
    import pymupdf
    from PIL import Image

    document = pymupdf.open(stream=_sheet(), filetype="pdf")
    page = document[0]
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    document.close()
    draft = tesseract_table(image)
    assert draft["warning"] is None, draft
    balloons = {row["balloon_no"] for row in draft["rows"]}
    assert "1" in balloons
    assert "2" in balloons
    readings = " ".join(f"{row.get('m1', '')} {row.get('m2', '')}" for row in draft["rows"])
    assert "120.05" in readings
    assert "10.02" in readings
