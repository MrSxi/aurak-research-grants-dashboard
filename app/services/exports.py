"""PDF, Excel and CSV exports.

A report is a title plus a list of blocks (key facts, paragraphs, tables). The same report is written to any of the
three formats, so every export in the dashboard looks and reads the same:
  - PDF:   AURAK-styled document (red header band, tables with a red heading row, page numbers)
  - Excel: a "Report" sheet for facts and text, one sheet per table (filters and frozen headings)
  - CSV:   UTF-8 (opens correctly in Excel). A single-table report is just that table.
"""
import csv
import io
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from fastapi import HTTPException
from fastapi.responses import Response

FORMATS = {"pdf": "application/pdf",
           "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
           "csv": "text/csv; charset=utf-8"}
RED = (172, 16, 15)
GOLD = (219, 169, 0)
GREY = (119, 119, 119)
PDF_MAX_ROWS = 300  # longer tables are cut in the PDF only; Excel and CSV always carry every row


@dataclass
class Report:
    title: str
    filename: str                      # without extension
    subtitle: str = ""
    blocks: list = field(default_factory=list)

    def facts(self, heading: str, pairs):
        self.blocks.append(("kv", heading, [(k, _s(v)) for k, v in pairs]))
        return self

    def text(self, heading: str, body):
        if body:
            self.blocks.append(("text", heading, _s(body)))
        return self

    def table(self, heading: str, columns: list, rows: list, note: str = ""):
        self.blocks.append(("table", heading, columns, [[_s(c) for c in r] for r in rows], note))
        return self


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d %b %Y %H:%M")
    if isinstance(v, date):
        return v.strftime("%d %b %Y")
    if isinstance(v, float):
        return f"{v:,.0f}" if v >= 1000 else (f"{v:g}")
    if isinstance(v, (list, tuple, set)):
        return "; ".join(_s(x) for x in v if x not in (None, ""))
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_s(x)}" for k, x in v.items() if x not in (None, "", [], {}))
    return str(v)


def respond(report: Report, fmt: str) -> Response:
    fmt = fmt.lower()
    if fmt not in FORMATS:
        raise HTTPException(404, "Unknown export format")
    body = {"pdf": _pdf, "xlsx": _xlsx, "csv": _csv}[fmt](report)
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", report.filename).strip("-")[:120] or "export"
    stamp = datetime.now().strftime("%Y-%m-%d")
    return Response(body, media_type=FORMATS[fmt],
                    headers={"Content-Disposition": f'attachment; filename="{name}-{stamp}.{fmt}"'})


# ---------------------------------------------------------------- CSV
def _csv(r: Report) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    tables = [b for b in r.blocks if b[0] == "table"]
    if len(r.blocks) == 1 and tables:          # plain table export
        w.writerow(tables[0][2])
        w.writerows(tables[0][3])
    else:
        w.writerow([r.title])
        if r.subtitle:
            w.writerow([r.subtitle])
        w.writerow([f"Generated {datetime.now():%d %b %Y %H:%M}"])
        for b in r.blocks:
            w.writerow([])
            w.writerow([b[1].upper()])
            if b[0] == "kv":
                w.writerows(b[2])
            elif b[0] == "text":
                w.writerow([b[2]])
            else:
                w.writerow(b[2])
                w.writerows(b[3])
    return out.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------- Excel
def _xlsx(r: Report) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="AC100F")
    wrap = Alignment(wrap_text=True, vertical="top")
    used = set()

    def sheet_name(text):
        base = re.sub(r"[\[\]:*?/\\]", " ", text).strip()[:31] or "Sheet"
        name, i = base, 2
        while name.lower() in used:
            name = f"{base[:28]} {i}"
            i += 1
        used.add(name.lower())
        return name

    ws = wb.active
    ws.title = sheet_name("Report")
    ws.append([r.title])
    ws["A1"].font = Font(bold=True, size=14, color="AC100F")
    if r.subtitle:
        ws.append([r.subtitle])
    ws.append([f"Generated {datetime.now():%d %b %Y %H:%M}"])
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 100
    for b in r.blocks:
        if b[0] == "table":
            continue
        ws.append([])
        ws.append([b[1]])
        ws.cell(ws.max_row, 1).font = Font(bold=True, size=12, color="AC100F")
        if b[0] == "kv":
            for k, v in b[2]:
                ws.append([k, v])
                ws.cell(ws.max_row, 1).font = Font(bold=True)
                ws.cell(ws.max_row, 2).alignment = wrap
        else:
            ws.append([b[2]])
            ws.cell(ws.max_row, 1).alignment = wrap
            ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=2)
            ws.row_dimensions[ws.max_row].height = min(400, 15 * (1 + len(b[2]) // 110))
    tables = [b for b in r.blocks if b[0] == "table"]
    if tables and len(r.blocks) == len(tables) and len(tables) == 1:
        wb.remove(ws)   # a plain table export needs no cover sheet
        used.discard("report")
    for b in tables:
        t = wb.create_sheet(sheet_name(b[1]))
        t.append(b[2])
        for c in t[1]:
            c.font, c.fill = head_font, head_fill
        for row in b[3]:
            t.append(row)
        for i, col in enumerate(b[2], 1):
            longest = max([len(col)] + [len(row[i - 1]) for row in b[3][:500] if i - 1 < len(row)])
            t.column_dimensions[get_column_letter(i)].width = max(10, min(60, longest + 2))
            if longest > 60:
                for cell in t.iter_rows(min_row=2, min_col=i, max_col=i):
                    cell[0].alignment = wrap
        t.freeze_panes = "A2"
        if b[3]:
            t.auto_filter.ref = t.dimensions
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ---------------------------------------------------------------- PDF
_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
]


def _pdf(r: Report) -> bytes:
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    wide = any(b[0] == "table" and len(b[2]) > 6 for b in r.blocks)

    class Doc(FPDF):
        def header(self):
            self.set_fill_color(*RED)
            self.rect(0, 0, self.w, 14, "F")
            self.set_fill_color(*GOLD)
            self.rect(0, 14, self.w, 1.2, "F")
            self.set_xy(self.l_margin, 4)
            self.set_font(FONT, "B", 11)
            self.set_text_color(255, 255, 255)
            self.cell(0, 6, "AURAK Research Grants Dashboard", align="L")
            self.set_font(FONT, "", 8)
            self.set_xy(self.l_margin, 4)
            self.cell(0, 6, "American University of Ras Al Khaimah", align="R")
            self.set_text_color(0, 0, 0)
            self.set_y(20)

        def footer(self):
            self.set_y(-12)
            self.set_font(FONT, "", 7.5)
            self.set_text_color(*GREY)
            self.cell(0, 6, f"Generated {datetime.now():%d %b %Y %H:%M} · Match scores rank research similarity; "
                            f"they are not probabilities.", align="L")
            self.cell(0, 6, f"Page {self.page_no()}/{{nb}}", align="R")

    pdf = Doc(orientation="L" if wide else "P", unit="mm", format="A4")
    FONT = "Helvetica"
    for reg, bold in _FONT_CANDIDATES:
        if os.path.exists(reg) and os.path.exists(bold):
            pdf.add_font("Body", "", reg)
            pdf.add_font("Body", "B", bold)
            FONT = "Body"
            break
    unicode_ok = FONT != "Helvetica"

    def clean(s: str, limit: int | None = None) -> str:
        s = s or ""
        if limit and len(s) > limit:
            s = s[: limit - 1].rstrip() + "…"
        if not unicode_ok:
            s = s.replace("·", "-").replace("…", "...").replace("“", '"').replace("”", '"').replace("–", "-")
            s = s.encode("latin-1", "replace").decode("latin-1")
        return s

    pdf.set_auto_page_break(True, margin=16)
    pdf.set_margins(14, 18, 14)
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_font(FONT, "B", 17)
    pdf.set_text_color(*RED)
    pdf.multi_cell(0, 8, clean(r.title), align="L", new_x="LMARGIN", new_y="NEXT")
    if r.subtitle:
        pdf.set_font(FONT, "", 10)
        pdf.set_text_color(*GREY)
        pdf.multi_cell(0, 5, clean(r.subtitle), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)
    head = FontFace(emphasis="BOLD", color=(255, 255, 255), fill_color=RED)

    def heading(text):
        if pdf.get_y() > pdf.h - 40:
            pdf.add_page()
        pdf.ln(3)
        pdf.set_font(FONT, "B", 12.5)
        pdf.set_text_color(51, 51, 51)
        pdf.cell(0, 7, clean(text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*GOLD)
        pdf.set_line_width(0.6)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + 30, pdf.get_y())
        pdf.ln(2)
        pdf.set_text_color(0, 0, 0)

    for b in r.blocks:
        heading(b[1])
        if b[0] == "kv":
            pdf.set_font(FONT, "", 9)
            with pdf.table(col_widths=(32, 98), text_align="LEFT", line_height=4.6, first_row_as_headings=False,
                           borders_layout="HORIZONTAL_LINES", padding=1.2) as t:
                for k, v in b[2]:
                    row = t.row()
                    row.cell(clean(k), style=FontFace(emphasis="BOLD"))
                    row.cell(clean(v, 1500) or "Not published")
        elif b[0] == "text":
            pdf.set_font(FONT, "", 9.5)
            pdf.multi_cell(0, 4.8, clean(b[2], 6000), new_x="LMARGIN", new_y="NEXT")
        else:
            cols, rows, note = b[2], b[3], b[4]
            if note:
                pdf.set_font(FONT, "", 8.5)
                pdf.set_text_color(*GREY)
                pdf.multi_cell(0, 4.2, clean(note), new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(0, 0, 0)
                pdf.ln(1)
            if not rows:
                pdf.set_font(FONT, "", 9)
                pdf.set_text_color(*GREY)
                pdf.cell(0, 5, "None.", new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(0, 0, 0)
                continue
            # a page fits about one column per 14 mm; wider tables keep the full set in Excel and CSV
            fit = int(pdf.epw // 14)
            if len(cols) > fit:
                pdf.set_font(FONT, "", 8.5)
                pdf.set_text_color(*GREY)
                pdf.multi_cell(0, 4.2, clean(f"The PDF shows the first {fit} of {len(cols)} columns. "
                                             f"Download Excel or CSV for every column."),
                               new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(0, 0, 0)
                cols = cols[:fit]
            # column widths follow typical content length (bounded), so long text columns get the room
            if len(rows) > PDF_MAX_ROWS:
                pdf.set_font(FONT, "", 8.5)
                pdf.set_text_color(*GREY)
                pdf.multi_cell(0, 4.2, clean(f"The PDF lists the first {PDF_MAX_ROWS} of {len(rows)} rows, in the "
                                             f"same order as the page. Download Excel or CSV for every row."),
                               new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(0, 0, 0)
                rows = rows[:PDF_MAX_ROWS]
            size = 8 if len(cols) <= 6 else 7
            # Column widths in mm. Each column first gets room for its longest ordinary word (so words do not
            # split across lines, at least 14 mm), and the rest of the page is shared by typical content length.
            weights, need = [], []
            for i, c in enumerate(cols):
                vals = [clean(rw[i]) if i < len(rw) else "" for rw in rows[:300]]
                lens = sorted(len(v) for v in vals)
                typical = lens[int(len(lens) * 0.8)] if lens else 0
                weights.append(max(6, min(45, max(len(c), typical))))
                words = {wd for v in vals for wd in v.split() if len(wd) <= 18}
                pdf.set_font(FONT, "", size)
                widest = max((pdf.get_string_width(wd) for wd in words), default=0)
                pdf.set_font(FONT, "B", size)
                widest = max([widest] + [pdf.get_string_width(wd) for wd in c.split() if len(wd) <= 18])
                need.append(max(14, widest + 3))
            spare = pdf.epw - sum(need)
            if spare >= 0:
                widths = [n + spare * w / sum(weights) for n, w in zip(need, weights)]
            else:
                widths = [n * pdf.epw / sum(need) for n in need]
            # keep every row shorter than a page: about 25 wrapped lines per cell at most
            limits = [max(60, int(wd / (size * 0.19) * 25)) for wd in widths]
            pdf.set_font(FONT, "", size)
            with pdf.table(col_widths=tuple(widths), text_align="LEFT", line_height=size * 0.48,
                           headings_style=head, borders_layout="HORIZONTAL_LINES", padding=1,
                           cell_fill_color=(247, 247, 247), cell_fill_mode="ROWS") as t:
                hr = t.row()
                for c in cols:
                    hr.cell(clean(c))
                for rw in rows:
                    row = t.row()
                    for i in range(len(cols)):
                        row.cell(clean(rw[i] if i < len(rw) else "", min(600, limits[i])))
    return bytes(pdf.output())
