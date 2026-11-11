"""PDF khái toán chi phí sơ bộ (+ bảng so sánh nhà thầu) — V2 "Tải PDF" (đặc tả mục 15).

Hai bước tách bạch:
- `build_report(...)`: hàm thuần (không DB/mạng/LLM) dựng `EstimateReport` từ JSON kết quả tool
  (EstimateResult snapshot + ComparisonMatrix). Mọi số lấy nguyên `EvidenceValue.display` hoặc kết quả của
  `app/formatting.py` trên dữ liệu đầu vào; không có văn bản LLM (nguyên tắc 2.1).
- `render_pdf(report)`: dàn trang bằng ReportLab với font Be Vietnam Pro (OFL, app/static/fonts) để hiển thị
  đủ dấu tiếng Việt. Mọi chuỗi đưa vào Paragraph đều được chuẩn hóa NFC và escape (tên nhà thầu có thể chứa
  `&`, `<`...). Đặt ở app/export (không phải app/engines) vì đọc file font/logo đi kèm.

Thương hiệu trên file: logo + "Vua thầu khái toán" + pro.vuathau.com.vn; ghi chú nguồn dữ liệu giữ "Dezon"
(dữ liệu thực sự lấy từ dezon.vn). File ẩn danh: không có tên/SĐT khách, chỉ có mã khái toán.
"""
from __future__ import annotations

import io
import threading
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from app.ai.fallback import PROJECT_COUNT_NOTE
from app.engines.estimate.explain import build_disclaimer
from app.evidence import STATUS_LABEL_VI, EvidenceValue, SourceRef
from app.formatting import NO_DATA, format_m2, vi_number
from app.schemas.common import BUILDING_TYPE_VI, WORK_SCOPE_VI
from app.schemas.estimate import EstimateResult
from app.schemas.export import (
    EstimateReport,
    ReportComparison,
    ReportComparisonRow,
    ReportContractor,
    ReportItemRow,
    ReportList,
    ReportRow,
    ReportSource,
)
from app.schemas.matching import ComparisonMatrix

BRAND_NAME = "Vua thầu khái toán"
SITE_URL = "https://pro.vuathau.com.vn"
SITE_LABEL = "pro.vuathau.com.vn"
# Việt Nam không dùng giờ mùa hè -> UTC+7 cố định, không phụ thuộc tzdata của hệ điều hành.
VN_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")
ESTIMATE_TITLE = "Khái toán chi phí sơ bộ"
STALE_MARK = " (*)"
STALE_NOTE = "(*) Dữ liệu có thể đã cũ — xem mục Nguồn dữ liệu."
# Một dòng bảng ReportLab không tách qua trang: ô quá dài (vd warranty_text thô ~1.000+ ký tự) -> LayoutError.
# 300 ký tự ở cột hẹp nhất (5 nhà thầu, trang ngang ≈ 113pt) ≈ 14 dòng, còn xa chiều cao khung trang.
MAX_CELL_CHARS = 300
MAX_NAME_CHARS = 120
TRUNCATED_NOTE = ("Ô có dấu \"…\" đã được rút gọn để vừa trang; nội dung đầy đủ xem trên hồ sơ nhà thầu tại Dezon "
                  "(dezon.vn).")
FOOTER_NOTE = "Khái toán sơ bộ, không phải báo giá thi công."

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
FONT_REGULAR_PATH = STATIC_DIR / "fonts" / "BeVietnamPro-Regular.ttf"
FONT_BOLD_PATH = STATIC_DIR / "fonts" / "BeVietnamPro-Bold.ttf"
LOGO_PATH = STATIC_DIR / "brand" / "vuathau-logo.png"
FONT = "BeVietnamPro"
FONT_BOLD = "BeVietnamPro-Bold"


# ================================================================================================
# Dựng nội dung (hàm thuần)
# ================================================================================================
def to_vn_time(dt: datetime) -> datetime:
    """Giờ Việt Nam; datetime không có múi giờ được coi là đã là giờ Việt Nam."""
    return dt.replace(tzinfo=VN_TZ) if dt.tzinfo is None else dt.astimezone(VN_TZ)


def format_datetime_vi(dt: datetime) -> str:
    """dd/mm/yyyy HH:mm theo giờ Việt Nam."""
    return to_vn_time(dt).strftime("%d/%m/%Y %H:%M")


def _date_vi(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else NO_DATA


def _source_line(ev: EvidenceValue) -> str | None:
    """"Dữ liệu tham khảo · Nguồn: … · cập nhật dd/mm/yyyy" (như dòng nguồn của EvidenceCell)."""
    if not ev.has_value:
        return None
    parts = [STATUS_LABEL_VI[ev.status]]
    if ev.source is not None:
        parts.append(f"Nguồn: {ev.source.label}")
        parts.append(f"cập nhật {ev.source.updated_at.strftime('%d/%m/%Y')}" if ev.source.updated_at
                     else f"ngày cập nhật: {NO_DATA}")
    if ev.is_stale:
        parts.append("dữ liệu có thể đã cũ")
    return " · ".join(parts)


def _display(ev: EvidenceValue | None) -> str:
    """Nguyên `display`; thiếu/rỗng -> "Chưa có dữ liệu" (không bao giờ để ô trống)."""
    if ev is None or not ev.has_value or not (ev.display or "").strip():
        return NO_DATA
    return ev.display


def _coefficient(k: float) -> str:
    s = vi_number(k, 2)
    return s if "," in s else f"{s},0"


def _floors_text(res: EstimateResult) -> str:
    inp = res.input
    extra = [t for flag, t in ((inp.has_mezzanine, "có tầng lửng"), (inp.basement, "có tầng hầm")) if flag]
    return f"{inp.floors} ({', '.join(extra)})" if extra else str(inp.floors)


def _land_text(res: EstimateResult) -> str:
    inp = res.input
    area = format_m2(inp.land_area_m2)
    if inp.land_width_m and inp.land_length_m:
        return f"{area} ({vi_number(inp.land_width_m)} × {vi_number(inp.land_length_m)} m)"
    return area


def _place(res: EstimateResult) -> str:
    return ", ".join(p for p in (res.input.district, res.input.province) if p)


def _collect_sources(values: list[EvidenceValue]) -> list[ReportSource]:
    """Nguồn khác nhau (label, url, ngày cập nhật) theo thứ tự xuất hiện; cờ cũ nếu một giá trị dùng nguồn đó đã cũ."""
    order: list[tuple[str, str | None, date | None]] = []
    stale: dict[tuple[str, str | None, date | None], bool] = {}
    for ev in values:
        if ev is None or not ev.has_value or ev.source is None:
            continue
        src: SourceRef = ev.source
        key = (src.label, src.url, src.updated_at)
        if key not in stale:
            order.append(key)
            stale[key] = False
        stale[key] = stale[key] or ev.is_stale
    return [ReportSource(label=label, url=url, updated_at=_date_vi(updated), is_stale=stale[(label, url, updated)])
            for label, url, updated in order]


def truncate_cell(text: str, limit: int = MAX_CELL_CHARS) -> str:
    """Rút gọn chuỗi dài quá `limit` ký tự (cắt ở ranh giới từ nếu được) + "…"; chuỗi ngắn giữ nguyên."""
    if len(text) <= limit:
        return text
    flat = " ".join(text.split())  # gộp khoảng trắng/xuống dòng để ô rút gọn không thêm dòng trống
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    space = cut.rfind(" ")
    if space >= limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:.-–—") + "…"


def _comparison(cmp: ComparisonMatrix, exported_at: str) -> ReportComparison:
    n = len(cmp.contractors)
    rows: list[ReportComparisonRow] = []
    any_stale = False
    any_truncated = False
    for row in cmp.rows:
        cells: list[str] = []
        for i in range(n):
            ev = row.cells[i] if i < len(row.cells) else None
            full = _display(ev)
            text = truncate_cell(full)
            any_truncated = any_truncated or (text != full and text.endswith("…"))
            if ev is not None and ev.has_value and ev.is_stale:
                text += STALE_MARK
                any_stale = True
            cells.append(text)
        rows.append(ReportComparisonRow(label=row.label_vi, cells=cells))
    notes = [
        f"Dữ liệu tại thời điểm xuất {exported_at}. Bảng so sánh được lập lại từ hồ sơ nhà thầu trên Dezon "
        "(dezon.vn) khi xuất file, nên có thể khác bảng anh/chị đã xem nếu dữ liệu vừa được cập nhật.",
        PROJECT_COUNT_NOTE,
        "Tên nhà thầu dẫn tới hồ sơ gốc trên dezon.vn. Mức phù hợp tính theo tiêu chí của anh/chị, "
        "không phải xếp hạng chất lượng nhà thầu.",
    ]
    if any_stale:
        notes.append(STALE_NOTE)
    if any_truncated:
        notes.append(TRUNCATED_NOTE)
    return ReportComparison(
        title=f"So sánh {n} nhà thầu",
        contractors=[ReportContractor(name=truncate_cell(c.name, MAX_NAME_CHARS), profile_url=c.profile_url or None)
                     for c in cmp.contractors],
        rows=rows,
        notes=notes,
    )


def build_report(estimate: EstimateResult, comparison: ComparisonMatrix | None, generated_at: datetime
                 ) -> EstimateReport:
    """Nội dung PDF từ khái toán đã lưu (+ bảng so sánh tính lại lúc xuất). Hàm thuần."""
    exported_at = format_datetime_vi(generated_at)
    code = (str(estimate.estimate_session_id or "").replace("-", "")[:8] or NO_DATA).upper()
    building = BUILDING_TYPE_VI.get(str(estimate.input.building_type), str(estimate.input.building_type))
    place = _place(estimate)

    project_rows = [ReportRow(label="Loại công trình", value=building),
                    ReportRow(label="Khu vực", value=place or NO_DATA),
                    ReportRow(label="Diện tích đất", value=_land_text(estimate))]
    if estimate.input.building_area_m2:
        project_rows.append(ReportRow(label="Diện tích xây dựng mỗi tầng",
                                      value=format_m2(estimate.input.building_area_m2)))
    project_rows += [ReportRow(label="Số tầng", value=_floors_text(estimate)),
                     ReportRow(label="Phạm vi thi công",
                               value=WORK_SCOPE_VI.get(str(estimate.input.work_scope), str(estimate.input.work_scope)))]

    rules_note = f"Hệ số khái toán phiên bản {estimate.rules_version} · {STATUS_LABEL_VI[estimate.rules_status]}"
    estimate_rows = [
        ReportRow(label="Diện tích sàn quy đổi", value=format_m2(estimate.converted_area_m2), note=rules_note),
        ReportRow(label="Đơn giá tham chiếu", value=_display(estimate.rate), note=_source_line(estimate.rate)),
        ReportRow(label="Chi phí sơ bộ", value=_display(estimate.cost), emphasize=True,
                  note="Dữ liệu có thể đã cũ" if estimate.cost.has_value and estimate.cost.is_stale else None),
    ]
    items = [ReportItemRow(label=it.label_vi, area=vi_number(it.area_m2), coefficient=_coefficient(it.coefficient),
                           converted=vi_number(it.converted_m2)) for it in estimate.items]
    lists = [ReportList(title=title, items=list(values), warn=warn)
             for title, values, warn in (("Lưu ý", estimate.warnings, True),
                                         ("Giả định", estimate.assumptions, False),
                                         ("Chưa bao gồm", estimate.exclusions, False),
                                         ("Yếu tố có thể làm tăng chi phí", estimate.cost_drivers, False))
             if values]
    disclaimer = (estimate.disclaimer or "").strip() or build_disclaimer(
        estimate.rate.source, has_rate=estimate.rate.has_value and estimate.cost_min is not None)

    cmp_block = _comparison(comparison, exported_at) if comparison is not None and comparison.contractors else None
    evidence = [estimate.rate, estimate.cost]
    if cmp_block is not None:
        evidence += [c for row in comparison.rows for c in row.cells]
    notes = [
        f"Số liệu khái toán giữ nguyên như khi em tính cho anh/chị (mã khái toán {code}); cờ dữ liệu cũ được "
        f"xét theo ngày xuất file.",
        f"File xuất từ {BRAND_NAME} ({SITE_LABEL}) lúc {exported_at} (giờ Việt Nam).",
    ]
    return EstimateReport(
        brand_name=BRAND_NAME, site_url=SITE_URL, site_label=SITE_LABEL,
        generated_at=f"Ngày xuất {exported_at}", estimate_code=code,
        title=ESTIMATE_TITLE if cmp_block is None else f"{ESTIMATE_TITLE} và so sánh nhà thầu",
        subtitle=" · ".join(p for p in (building, place) if p) or None,
        project_rows=project_rows, estimate_rows=estimate_rows,
        items_title="Chi tiết hệ số quy đổi", items=items, items_total=format_m2(estimate.converted_area_m2),
        lists=lists, disclaimer=disclaimer, comparison=cmp_block,
        sources=_collect_sources(evidence), notes=notes,
    )


def report_texts(report: EstimateReport) -> list[str]:
    """Mọi chuỗi hiển thị trong file (kiểm tra guardrail/font trong test)."""
    out: list[str] = [report.brand_name, report.site_label, report.generated_at, report.estimate_code, report.title,
                      report.subtitle or "", report.items_title, report.items_total, report.disclaimer, FOOTER_NOTE,
                      *report.notes]
    for r in [*report.project_rows, *report.estimate_rows]:
        out += [r.label, r.value, r.note or ""]
    for it in report.items:
        out += [it.label, it.area, it.coefficient, it.converted]
    for lst in report.lists:
        out += [lst.title, *lst.items]
    if report.comparison is not None:
        out += [report.comparison.title, *(c.name for c in report.comparison.contractors), *report.comparison.notes]
        for row in report.comparison.rows:
            out += [row.label, *row.cells]
    for s in report.sources:
        out += [s.label, s.url or "", s.updated_at]
    return [t for t in out if t]


# ================================================================================================
# Dàn trang PDF (ReportLab)
# ================================================================================================
NAVY = "#001c33"
ACCENT = "#ffb51c"
MUTED = "#5b6573"
LINE = "#d9dee5"
LINK = "#0b5cad"

_font_lock = threading.Lock()


def register_fonts() -> None:
    """Đăng ký Be Vietnam Pro (Regular/Bold) một lần cho ReportLab; `<b>` trong Paragraph dùng bản Bold."""
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    with _font_lock:
        if FONT_BOLD in pdfmetrics.getRegisteredFontNames():
            return
        pdfmetrics.registerFont(TTFont(FONT, str(FONT_REGULAR_PATH)))
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONT_BOLD_PATH)))
        for bold, italic, name in ((0, 0, FONT), (1, 0, FONT_BOLD), (0, 1, FONT), (1, 1, FONT_BOLD)):
            addMapping(FONT, bold, italic, name)


def clean(text: str | None) -> str:
    """Chuỗi thuần cho canvas: NFC (chữ dựng sẵn) + bỏ ký tự điều khiển."""
    s = unicodedata.normalize("NFC", text or "")
    return "".join(ch for ch in s if ch in "\n\t" or unicodedata.category(ch)[0] != "C")


def markup(text: str | None) -> str:
    """Chuỗi an toàn cho Paragraph (mini-HTML của ReportLab): NFC + escape &, <, >."""
    return escape(clean(text)).replace("\n", "<br/>")


def _link(url: str | None, text: str) -> str:
    safe = markup(text)
    if not url or not url.startswith(("https://", "http://")):
        return safe
    href = escape(clean(url), {'"': "&quot;"})
    return f'<a href="{href}" color="{LINK}">{safe}</a>'


def render_pdf(report: EstimateReport) -> bytes:
    """PDF A4 (bảng so sánh > 3 nhà thầu sang trang ngang). Đầu trang: logo, thương hiệu, ngày xuất, mã khái toán;
    chân trang: ghi chú "không phải báo giá" + "Trang x/y". `invariant` -> cùng nội dung cho cùng byte."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import (
        BaseDocTemplate,
        CondPageBreak,
        Frame,
        KeepTogether,
        NextPageTemplate,
        PageBreak,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    register_fonts()
    margin = 16 * mm
    header_h = 24 * mm
    footer_h = 12 * mm

    base = ParagraphStyle("base", fontName=FONT, fontSize=9.5, leading=13.5, textColor=colors.HexColor("#1f2933"))
    styles = {
        "title": ParagraphStyle("title", parent=base, fontName=FONT_BOLD, fontSize=16, leading=21,
                                textColor=colors.HexColor(NAVY), spaceAfter=2),
        "sub": ParagraphStyle("sub", parent=base, textColor=colors.HexColor(MUTED), spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base, fontName=FONT_BOLD, fontSize=11.5, leading=15,
                             textColor=colors.HexColor(NAVY), spaceBefore=10, spaceAfter=4),
        "body": base,
        "bold": ParagraphStyle("bold", parent=base, fontName=FONT_BOLD),
        "em": ParagraphStyle("em", parent=base, fontName=FONT_BOLD, fontSize=11.5, leading=15),
        "small": ParagraphStyle("small", parent=base, fontSize=7.8, leading=10.5, textColor=colors.HexColor(MUTED)),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.4, leading=11),
        "cellb": ParagraphStyle("cellb", parent=base, fontName=FONT_BOLD, fontSize=8.4, leading=11),
        "num": ParagraphStyle("num", parent=base, fontSize=8.4, leading=11, alignment=TA_RIGHT),
        "numb": ParagraphStyle("numb", parent=base, fontName=FONT_BOLD, fontSize=8.4, leading=11, alignment=TA_RIGHT),
        "bullet": ParagraphStyle("bullet", parent=base, leftIndent=11, bulletIndent=2, spaceAfter=1),
        "disc": ParagraphStyle("disc", parent=base, fontSize=9, leading=12.5, textColor=colors.HexColor("#78350f")),
    }

    def P(text: str | None, style: str = "body") -> Paragraph:
        return Paragraph(markup(text), styles[style])

    grid = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(LINE)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])

    def kv_table(rows: list[ReportRow], width: float) -> Table:
        data = []
        for r in rows:
            value: list = [P(r.value, "em" if r.emphasize else "body")]
            if r.note:
                value.append(P(r.note, "small"))
            data.append([P(r.label, "bold"), value])
        t = Table(data, colWidths=[width * 0.34, width * 0.66])
        t.setStyle(grid)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f6f9"))]))
        for i, r in enumerate(rows):
            if r.emphasize:
                t.setStyle(TableStyle([("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fff6df"))]))
        return t

    def items_table(width: float) -> Table:
        header = [P("Hạng mục", "cellb"), Paragraph("A<sub>i</sub> (m²)", styles["numb"]),
                  Paragraph("k<sub>i</sub>", styles["numb"]), Paragraph("A<sub>i</sub>×k<sub>i</sub>", styles["numb"])]
        data = [header] + [[P(it.label, "cell"), P(it.area, "num"), P(it.coefficient, "num"), P(it.converted, "num")]
                           for it in report.items]
        data.append([P("Tổng diện tích quy đổi", "cellb"), "", "", P(report.items_total, "numb")])
        t = Table(data, colWidths=[width * 0.52, width * 0.16, width * 0.14, width * 0.18], repeatRows=1)
        t.setStyle(grid)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                               ("SPAN", (0, -1), (2, -1)),
                               ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f4f6f9"))]))
        return t

    def comparison_table(cmp: ReportComparison, width: float) -> Table:
        n = max(1, len(cmp.contractors))
        first = width * (0.24 if n <= 3 else 0.18)
        col = (width - first) / n
        header = [P("Tiêu chí", "cellb")] + [Paragraph(f"<b>{_link(c.profile_url, c.name)}</b>", styles["cell"])
                                             for c in cmp.contractors]
        data = [header] + [[P(row.label, "cellb")] + [P(c, "cell") for c in row.cells] for row in cmp.rows]
        t = Table(data, colWidths=[first] + [col] * n, repeatRows=1)
        t.setStyle(grid)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                               ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#f4f6f9"))]))
        return t

    def disclaimer_box(width: float) -> Table:
        t = Table([[Paragraph(f"<b>Lưu ý quan trọng: </b>{markup(report.disclaimer)}", styles["disc"])]],
                  colWidths=[width])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fffbeb")),
                               ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor("#f59e0b")),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        return t

    def decorate(canv: Canvas, doc: BaseDocTemplate) -> None:
        pw, ph = canv._pagesize
        canv.saveState()
        band = 15 * mm
        canv.setFillColor(colors.HexColor(NAVY))
        canv.rect(0, ph - band, pw, band, stroke=0, fill=1)
        logo_h = 7.5 * mm
        canv.drawImage(str(LOGO_PATH), margin, ph - band + (band - logo_h) / 2, width=logo_h * 360 / 83,
                       height=logo_h, mask="auto")
        canv.setFillColor(colors.white)
        canv.setFont(FONT_BOLD, 11)
        canv.drawRightString(pw - margin, ph - band + 8.6 * mm, clean(report.brand_name))
        canv.setFillColor(colors.HexColor(ACCENT))
        canv.setFont(FONT, 8)
        canv.drawRightString(pw - margin, ph - band + 4 * mm, clean(report.site_label))
        canv.linkURL(report.site_url, (pw - margin - 40 * mm, ph - band + 2 * mm, pw - margin, ph - band + 7 * mm),
                     relative=0)
        canv.setFillColor(colors.HexColor(MUTED))
        canv.setFont(FONT, 8)
        canv.drawString(margin, ph - band - 5 * mm, clean(report.generated_at))
        canv.drawRightString(pw - margin, ph - band - 5 * mm, clean(f"Mã khái toán: {report.estimate_code}"))
        canv.setStrokeColor(colors.HexColor(LINE))
        canv.setLineWidth(0.5)
        canv.line(margin, footer_h, pw - margin, footer_h)
        canv.drawString(margin, footer_h - 4.5 * mm, clean(f"{report.brand_name} · {report.site_label} — {FOOTER_NOTE}"))
        canv.restoreState()

    class NumberedCanvas(Canvas):
        """Vẽ "Trang x/y" khi đã biết tổng số trang."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages: list[dict] = []

        def showPage(self):  # noqa: N802 — API ReportLab
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for i, state in enumerate(self._pages, start=1):
                self.__dict__.update(state)
                pw, _ = self._pagesize
                self.setFont(FONT, 8)
                self.setFillColor(colors.HexColor(MUTED))
                self.drawRightString(pw - margin, footer_h - 4.5 * mm, f"Trang {i}/{total}")
                super().showPage()
            super().save()

    def frame_for(size: tuple[float, float], fid: str) -> Frame:
        w, h = size
        return Frame(margin, footer_h + 3 * mm, w - 2 * margin, h - header_h - footer_h - 6 * mm, id=fid,
                     leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    portrait, wide = A4, landscape(A4)
    buf = io.BytesIO()
    doc = BaseDocTemplate(
        buf, pagesize=portrait, invariant=1, title=clean(f"{report.title} — {report.estimate_code}"),
        author=clean(report.brand_name), creator=clean(report.brand_name), subject=clean(report.generated_at),
        leftMargin=margin, rightMargin=margin, topMargin=header_h, bottomMargin=footer_h,
        pageTemplates=[PageTemplate(id="portrait", frames=[frame_for(portrait, "p")], pagesize=portrait,
                                    onPage=decorate),
                       PageTemplate(id="landscape", frames=[frame_for(wide, "l")], pagesize=wide, onPage=decorate)],
    )
    width = portrait[0] - 2 * margin

    story: list = [P(report.title, "title")]
    if report.subtitle:
        story.append(P(report.subtitle, "sub"))
    story += [P("Thông tin công trình", "h2"), kv_table(report.project_rows, width),
              P("Kết quả khái toán", "h2"), kv_table(report.estimate_rows, width),
              KeepTogether([P(report.items_title, "h2"), items_table(width)])]
    for lst in report.lists:
        bullets = [Paragraph(markup(t), styles["bullet"], bulletText="•") for t in lst.items]
        # Giữ tiêu đề cùng hai dòng đầu (không để tiêu đề mồ côi cuối trang).
        story += [KeepTogether([P(lst.title, "h2"), *bullets[:2]]), *bullets[2:]]
    story += [Spacer(1, 8), disclaimer_box(width)]

    if report.comparison is not None:
        cmp = report.comparison
        if len(cmp.contractors) > 3:
            story += [NextPageTemplate("landscape"), PageBreak()]
            cmp_width = wide[0] - 2 * margin
        else:
            story.append(CondPageBreak(60 * mm))
            cmp_width = width
        story += [P(cmp.title, "h2"), comparison_table(cmp, cmp_width), Spacer(1, 4)]
        story += [P(n, "small") for n in cmp.notes]

    if report.sources:
        story.append(P("Nguồn dữ liệu", "h2"))
        for s in report.sources:
            line = f"{_link(s.url, s.label)} · cập nhật {markup(s.updated_at)}" if s.updated_at != NO_DATA \
                else f"{_link(s.url, s.label)} · ngày cập nhật: {markup(NO_DATA)}"
            if s.is_stale:
                line += " · <b>dữ liệu có thể đã cũ</b>"
            story.append(Paragraph(line, styles["bullet"], bulletText="•"))
    story.append(Spacer(1, 8))
    story += [P(n, "small") for n in report.notes]

    doc.build(story, canvasmaker=NumberedCanvas)
    return buf.getvalue()
