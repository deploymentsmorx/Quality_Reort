from __future__ import annotations

import csv
import io
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.checks import evaluate

HEADERS = [
    "Sl no",
    "Parameter Description",
    "Dim",
    "Tol",
    "Instrument Used",
    "Actual (T/W)",
    "Actual (F/W)",
    "Result",
    "Status",
    "Check",
]

RESULT_LABEL = {
    "pass": "In range",
    "boundary": "On limit",
    "fail": "Out of range",
    "review": "Check",
    "incomplete": "Missed",
    "missed": "Missed",
    "empty": "Empty",
    "na": "-",
}

FILLS = {
    "pass": "E5F4EA",
    "boundary": "FFE3C2",
    "fail": "FDE8E6",
    "review": "FFF4DC",
    "incomplete": "F3F0EA",
    "missed": "F3F0EA",
    "empty": "FFFFFF",
}


def _num(value) -> str:
    if value is None or value == "":
        return ""
    number = float(value)
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _tol(row: dict) -> str:
    plus = row.get("tol_plus")
    minus = row.get("tol_minus")
    if plus in (None, "") and minus in (None, ""):
        return "-"
    plus_text = _num(plus) if plus not in (None, "") else "0"
    minus_text = _num(minus) if minus not in (None, "") else "0"
    if plus_text == minus_text:
        return f"±{plus_text}"
    return f"+{plus_text}/-{minus_text}"


def row_values(row: dict, instruments: list[dict], blank_measurements: bool) -> list[str]:
    verdict = evaluate(row, instruments)
    return [
        str(row.get("balloon_no") or ""),
        str(row.get("characteristic") or ""),
        str(row.get("nominal") or ""),
        _tol(row),
        str(row.get("instrument") or ""),
        "" if blank_measurements else (row.get("m1") or ""),
        "" if blank_measurements else (row.get("m2") or ""),
        "" if blank_measurements else (row.get("sheet_result") or ""),
        "" if blank_measurements else RESULT_LABEL.get(verdict.result, ""),
        "" if blank_measurements else "; ".join(verdict.reasons),
    ]


def filter_rows(rows: list[dict], scope: str, page: int | None, work_area: str | None) -> list[dict]:
    chosen = rows
    if scope == "page" and page:
        chosen = [row for row in chosen if int(row.get("page_no") or 0) == int(page)]
    elif scope == "area" and work_area:
        target = work_area.strip().casefold()
        chosen = [row for row in chosen if str(row.get("work_area") or "").strip().casefold() == target]
    return chosen


def build_csv(job: dict, rows: list[dict], instruments: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Company", job.get("company_name", ""), "Doc", job.get("doc_number", "")])
    writer.writerow(["Part number", job.get("part_number", ""), "Part name", job.get("part_name", ""), "Rev", job.get("revision", "")])
    writer.writerow(["Customer", job.get("customer", ""), "Date", job.get("report_date", ""), "Serial", job.get("serial_number", "")])
    writer.writerow(["Drawing", job.get("drawing_number", ""), "Material", job.get("material", ""), "Finish", job.get("finish_treatment", "")])
    writer.writerow(["Mass", job.get("mass", ""), "Supplier", job.get("supplier_name", ""), "Signatory", job.get("authorized_signatory", "")])
    writer.writerow([])
    writer.writerow(HEADERS)
    for row in rows:
        writer.writerow(row_values(row, instruments, blank_measurements=False))
    return buffer.getvalue().encode("utf-8-sig")


def _sheet_border() -> Border:
    line = Side(style="thin", color="1C2430")
    return Border(left=line, right=line, top=line, bottom=line)


def _write_merged(sheet, row: int, start: int, end: int, text: str, *, bold: bool = False, size: int = 11) -> None:
    sheet.merge_cells(start_row=row, start_column=start, end_row=row, end_column=end)
    border = _sheet_border()
    font = Font(name="Calibri", bold=bold, size=size)
    alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for column in range(start, end + 1):
        cell = sheet.cell(row, column)
        cell.border = border
        cell.font = font
        cell.alignment = alignment
    sheet.cell(row, start, text)


def _write_identity(sheet, job: dict) -> None:
    company = str(job.get("company_name") or "Ingenious engineering Pvt Ltd").strip()
    doc_no = str(job.get("doc_number") or "").strip()
    _write_merged(sheet, 1, 1, 7, company, bold=True, size=16)
    _write_merged(sheet, 1, 8, 10, f"Doc No: {doc_no}" if doc_no else "Doc No:", size=12)
    _write_merged(sheet, 2, 1, 2, "Inspection report :")
    _write_merged(sheet, 2, 3, 5, "CTQ Inspection report", bold=True, size=13)
    _write_merged(sheet, 2, 6, 7, "Part name :")
    _write_merged(sheet, 2, 8, 10, str(job.get("part_name") or ""))
    _write_merged(sheet, 3, 1, 2, "Customer :")
    _write_merged(sheet, 3, 3, 5, str(job.get("customer") or ""), bold=True)
    _write_merged(sheet, 3, 6, 7, "Date :")
    _write_merged(sheet, 3, 8, 10, str(job.get("report_date") or ""), bold=True)
    _write_merged(sheet, 4, 1, 2, "Drawing Number :")
    _write_merged(sheet, 4, 3, 5, str(job.get("drawing_number") or ""), bold=True)
    _write_merged(sheet, 4, 6, 7, "Sl no :")
    _write_merged(sheet, 4, 8, 10, str(job.get("serial_number") or ""), bold=True)
    sheet.row_dimensions[1].height = 28
    for row in (2, 3, 4):
        sheet.row_dimensions[row].height = 22


def build_xlsx(job: dict, rows: list[dict], instruments: list[dict]) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Inspection"
    _write_identity(sheet, job)
    header_row = 6
    sheet.append([])
    sheet.append(HEADERS)
    header_fill = PatternFill("solid", fgColor="1C2430")
    header_font = Font(color="FFFFFF", bold=True, name="Calibri", size=10)
    thin = Border(
        left=Side(style="thin", color="D9D0C3"),
        right=Side(style="thin", color="D9D0C3"),
        top=Side(style="thin", color="D9D0C3"),
        bottom=Side(style="thin", color="D9D0C3"),
    )
    for cell in sheet[header_row]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center", horizontal="center")
    for row in rows:
        verdict = evaluate(row, instruments)
        values = row_values(row, instruments, blank_measurements=False)
        sheet.append(values)
        fill = PatternFill("solid", fgColor=FILLS.get(verdict.result, "FFFFFF"))
        for cell in sheet[sheet.max_row]:
            cell.fill = fill
            cell.border = thin
            cell.font = Font(name="Calibri", size=10)
            cell.alignment = Alignment(vertical="center", horizontal="center")
    widths = [14, 18, 16, 14, 16, 16, 16, 14, 14, 36]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.auto_filter.ref = f"A{header_row}:J{max(sheet.max_row, header_row)}"
    sheet.freeze_panes = f"A{header_row + 1}"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.oddHeader.left.text = f"IE Inspection  {job.get('job_number', '')}"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _identity_table(job: dict, width: float, pages: int) -> Table:
    del pages
    company = ParagraphStyle("company", fontName="Helvetica-Bold", fontSize=14, leading=18, alignment=1, textColor=colors.black)
    doc_style = ParagraphStyle("docno", fontName="Helvetica", fontSize=10, leading=13, alignment=1, textColor=colors.black)
    report_title = ParagraphStyle("reporttitle", fontName="Helvetica-Bold", fontSize=12, leading=15, alignment=1, textColor=colors.black)
    label = ParagraphStyle("idlabel", fontName="Helvetica", fontSize=9, leading=12, textColor=colors.black)
    value = ParagraphStyle("idvalue", fontName="Helvetica-Bold", fontSize=10, leading=13, alignment=1, textColor=colors.black)

    def shown(text) -> str:
        cleaned = _xml(text).strip()
        return cleaned or "&nbsp;"

    label_w = 118
    label2_w = 78
    value2_w = 160
    value1_w = width - label_w - label2_w - value2_w
    doc_no = str(job.get("doc_number") or "").strip()
    rows = [
        [
            Paragraph(shown(job.get("company_name") or "Ingenious engineering Pvt Ltd"), company),
            "",
            "",
            Paragraph(shown(f"Doc No: {doc_no}" if doc_no else "Doc No:"), doc_style),
        ],
        [
            Paragraph("Inspection report :", label),
            Paragraph("CTQ Inspection report", report_title),
            Paragraph("Part name :", label),
            Paragraph(shown(job.get("part_name")), value),
        ],
        [
            Paragraph("Customer :", label),
            Paragraph(shown(job.get("customer")), value),
            Paragraph("Date :", label),
            Paragraph(shown(job.get("report_date")), value),
        ],
        [
            Paragraph("Drawing Number :", label),
            Paragraph(shown(job.get("drawing_number")), value),
            Paragraph("Sl no :", label),
            Paragraph(shown(job.get("serial_number")), value),
        ],
    ]
    table = Table(rows, colWidths=[label_w, value1_w, label2_w, value2_w])
    table.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (2, 0)),
                ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, 0), 8),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                ("TOPPADDING", (0, 1), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("ALIGN", (1, 1), (1, -1), "CENTER"),
                ("ALIGN", (3, 1), (3, -1), "CENTER"),
            ]
        )
    )
    return table


def _signature_draw_size(seal_path: str, usable: float) -> tuple[float, float]:
    reader = ImageReader(seal_path)
    width, height = reader.getSize()
    if not width or not height:
        return usable, 70
    draw_w = usable
    draw_h = draw_w * height / width
    max_h = 150
    if draw_h > max_h:
        draw_h = max_h
        draw_w = draw_h * width / height
    return draw_w, draw_h


def build_pdf(
    job: dict,
    rows: list[dict],
    instruments: list[dict],
    blank_measurements: bool,
    seal_path: str | None = None,
) -> bytes:
    pages = 1
    content = b""
    for _ in range(2):
        content, pages = _render_pdf(job, rows, instruments, blank_measurements, pages, seal_path)
    return content


def _render_pdf(
    job: dict,
    rows: list[dict],
    instruments: list[dict],
    blank_measurements: bool,
    pages: int,
    seal_path: str | None = None,
) -> tuple[bytes, int]:
    buffer = io.BytesIO()
    signature = None
    bottom = 32
    if seal_path and Path(seal_path).is_file():
        draw_w, draw_h = _signature_draw_size(seal_path, landscape(A4)[0] - 56)
        signature = (seal_path, draw_w, draw_h)
        bottom = 22 + draw_h + 12
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=28,
        rightMargin=28,
        topMargin=28,
        bottomMargin=bottom,
        title=f"{job.get('job_number', 'Inspection')} report",
    )
    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=10, textColor=colors.HexColor("#1C2430"))
    head = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=7.5, leading=9, textColor=colors.white)
    usable = landscape(A4)[0] - 56
    story = [_identity_table(job, usable, pages), Spacer(1, 8)]
    shown_headers = HEADERS[:8] if blank_measurements else HEADERS
    header = [Paragraph(label, head) for label in shown_headers]
    data = [header]
    verdicts = []
    for row in rows:
        values = row_values(row, instruments, blank_measurements=blank_measurements)
        shown = values[:8] if blank_measurements else values
        data.append([Paragraph(_xml(value) or "&nbsp;", cell) for value in shown])
        verdicts.append(evaluate(row, instruments).result)

    raw = [42, 150, 70, 48, 70, 78, 78, 52, 58]
    if not blank_measurements:
        raw = [32, 100, 50, 38, 54, 62, 62, 48, 52, 100]
    scale = usable / sum(raw)
    widths = [item * scale for item in raw]
    table = Table(data, colWidths=widths, repeatRows=1)
    style_commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1C2430")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, 0), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#1C2430")),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 5 if blank_measurements else 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5 if blank_measurements else 3),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
    ]
    if not blank_measurements:
        colour = {
            "pass": colors.HexColor("#E5F4EA"),
            "boundary": colors.HexColor("#FFE3C2"),
            "fail": colors.HexColor("#FDE8E6"),
            "review": colors.HexColor("#FFF4DC"),
            "incomplete": colors.HexColor("#F3F0EA"),
            "missed": colors.HexColor("#F3F0EA"),
            "empty": colors.white,
        }
        for index, result in enumerate(verdicts, start=1):
            style_commands.append(("BACKGROUND", (0, index), (-1, index), colour.get(result, colors.white)))
    table.setStyle(TableStyle(style_commands))
    story.append(table)
    seen = {"pages": 1}

    def remember(canvas, doc):
        seen["pages"] = doc.page
        _footer(job, signature)(canvas, doc)

    document.build(story, onFirstPage=remember, onLaterPages=remember)
    return buffer.getvalue(), seen["pages"]


def _xml(value: str) -> str:
    return (
        str(value)
        .replace("–", "-")
        .replace("—", "-")
        .replace("±", "+/-")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _footer(job: dict, signature: tuple | None):
    def draw(canvas, doc):
        canvas.saveState()
        if signature:
            path, draw_w, draw_h = signature
            page_w = landscape(A4)[0]
            x = 28 + ((page_w - 56) - draw_w) / 2
            canvas.drawImage(path, x, 22, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#1C2430"))
        canvas.drawString(28, 10, f"IE Inspection  {job.get('job_number', '')}  {job.get('part_number', '')}")
        canvas.drawRightString(landscape(A4)[0] - 28, 10, f"Page {doc.page}")
        canvas.restoreState()

    return draw
