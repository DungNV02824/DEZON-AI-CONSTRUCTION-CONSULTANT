"""V2 — Xuất PDF khái toán (+ so sánh nhà thầu): dựng nội dung (thuần), dàn trang ReportLab, API GET .../pdf."""
import re
import unicodedata
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.ai import blocks, tools
from app.db.models import AnalyticsEvent, Contractor
from app.engines.estimate import calculate_estimate
from app.evidence import EvidenceValue, SourceRef, VerificationStatus
from app.export import estimate_pdf as pdf
from app.formatting import NO_DATA, format_m2
from app.schemas.matching import ComparedContractor, ComparisonMatrix, ComparisonRow
from app.services import export_service
from tests.test_estimate_engine import TODAY, make_input, make_rate, make_rules

EXPORTED = datetime(2026, 11, 11, 2, 5, tzinfo=UTC)  # = 11/11/2026 09:05 giờ Việt Nam
SID = uuid.UUID("ab12cd34-0000-4000-8000-000000000001")
PROFILE_SRC = SourceRef(label="Hồ sơ nhà thầu trên Dezon", url="https://dezon.vn/nha-thau/a", updated_at=date(2026, 3, 1))


def _estimate(rate=True, **inp):
    res = calculate_estimate(make_input(**inp), make_rules(), make_rate() if rate else None, today=TODAY)
    return res.model_copy(update={"estimate_session_id": SID})


def _ev(display: str, *, stale: bool = False, src: SourceRef = PROFILE_SRC) -> EvidenceValue:
    return EvidenceValue(value=display, display=display, status=VerificationStatus.on_dezon, source=src,
                         is_stale=stale)


def _matrix(n: int = 3, name0: str = "Công ty A & B <Xây dựng>") -> ComparisonMatrix:
    contractors = [ComparedContractor(id=i + 1, slug=f"nt-{i}", name=name0 if i == 0 else f"Nhà thầu Hưng Thịnh {i}",
                                      profile_url=f"https://dezon.vn/nha-thau/nt-{i}") for i in range(n)]
    rows = [
        ComparisonRow(key="area", label_vi="Khu vực", cells=[_ev("TP. Hồ Chí Minh") for _ in range(n)]),
        ComparisonRow(key="dezon_project_count", label_vi="Công trình trên Dezon",
                      cells=[_ev("12 công trình ghi nhận trên Dezon", stale=True)] + [EvidenceValue.unavailable()] * (n - 1)),
        ComparisonRow(key="warranty", label_vi="Bảo hành", cells=[EvidenceValue.unavailable()] * (n - 1)),  # thiếu 1 ô
    ]
    return ComparisonMatrix(contractors=contractors, rows=rows)


# ------------------------------------------------------------------ dựng nội dung (thuần)
def test_report_header_brand_and_export_time():
    rep = pdf.build_report(_estimate(), None, EXPORTED)
    assert rep.brand_name == "Vua thầu khái toán" and rep.site_label == "pro.vuathau.com.vn"
    assert rep.generated_at == "Ngày xuất 11/11/2026 09:05"
    assert rep.estimate_code == "AB12CD34"
    assert rep.title == "Khái toán chi phí sơ bộ" and rep.comparison is None
    assert rep.subtitle == "Nhà phố · TP. Hồ Chí Minh"


def test_report_values_are_engine_display_strings():
    est = _estimate()
    rep = pdf.build_report(est, None, EXPORTED)
    rows = {r.label: r for r in rep.estimate_rows}
    assert rows["Đơn giá tham chiếu"].value == est.rate.display
    assert rows["Chi phí sơ bộ"].value == est.cost.display and rows["Chi phí sơ bộ"].emphasize
    assert rows["Diện tích sàn quy đổi"].value == format_m2(est.converted_area_m2)
    assert "Nguồn: Đơn giá tham khảo (test)" in rows["Đơn giá tham chiếu"].note
    assert "cập nhật 31/10/2026" in rows["Đơn giá tham chiếu"].note
    assert rep.disclaimer == est.disclaimer
    assert [it.label for it in rep.items] == [it.label_vi for it in est.items]
    assert rep.items_total == format_m2(est.converted_area_m2)
    assert {lst.title for lst in rep.lists} >= {"Giả định", "Chưa bao gồm"}
    assert [s.label for s in rep.sources] == ["Đơn giá tham khảo (test)"]  # rate & cost cùng nguồn -> 1 dòng
    proj = {r.label: r.value for r in rep.project_rows}
    assert proj["Diện tích đất"] == "100 m²" and proj["Phạm vi thi công"] == "Xây thô" and proj["Số tầng"] == "4"


def test_report_without_rate_keeps_no_data_and_no_rate_disclaimer():
    est = _estimate(rate=False)
    rep = pdf.build_report(est, None, EXPORTED)
    rows = {r.label: r for r in rep.estimate_rows}
    assert rows["Chi phí sơ bộ"].value == NO_DATA == "Chưa có dữ liệu"
    assert rows["Đơn giá tham chiếu"].value == NO_DATA
    assert "chưa tính được chi phí" in rep.disclaimer
    assert rep.sources == []


def test_report_empty_disclaimer_falls_back():
    est = _estimate().model_copy(update={"disclaimer": "  "})
    rep = pdf.build_report(est, None, EXPORTED)
    assert rep.disclaimer.startswith("Đây là khái toán sơ bộ") and "Không phải báo giá thi công" in rep.disclaimer


def test_report_dimensions_and_floors_text():
    rep = pdf.build_report(_estimate(land_area_m2=None, land_width_m=5, land_length_m=20, basement=True,
                                     has_mezzanine=True, district="Quận 7"), None, EXPORTED)
    proj = {r.label: r.value for r in rep.project_rows}
    assert proj["Diện tích đất"] == "100 m² (5 × 20 m)"
    assert proj["Số tầng"] == "4 (có tầng lửng, có tầng hầm)"
    assert proj["Khu vực"] == "Quận 7, TP. Hồ Chí Minh"


def test_report_comparison_cells_never_empty_and_stale_marked():
    rep = pdf.build_report(_estimate(), _matrix(), EXPORTED)
    cmp = rep.comparison
    assert cmp is not None and cmp.title == "So sánh 3 nhà thầu"
    assert rep.title == "Khái toán chi phí sơ bộ và so sánh nhà thầu"
    assert all(len(r.cells) == 3 and all(c.strip() for c in r.cells) for r in cmp.rows)
    warranty = next(r for r in cmp.rows if r.label == "Bảo hành")
    assert warranty.cells == [NO_DATA] * 3
    count = next(r for r in cmp.rows if r.label == "Công trình trên Dezon")
    assert count.cells[0] == "12 công trình ghi nhận trên Dezon" + pdf.STALE_MARK
    assert any(n.startswith("Dữ liệu tại thời điểm xuất 11/11/2026 09:05") for n in cmp.notes)
    assert any("công trình ghi nhận trên Dezon" in n for n in cmp.notes)
    assert pdf.STALE_NOTE in cmp.notes
    src = {s.label: s for s in rep.sources}
    assert src["Hồ sơ nhà thầu trên Dezon"].is_stale and src["Hồ sơ nhà thầu trên Dezon"].updated_at == "01/03/2026"
    assert not src["Đơn giá tham khảo (test)"].is_stale


def test_report_has_no_forbidden_wording_and_no_customer_data():
    rep = pdf.build_report(_estimate(), _matrix(5), EXPORTED)
    text = " ".join(pdf.report_texts(rep)).lower()
    assert "tốt nhất" not in text
    assert "dezon" in text  # nguồn dữ liệu giữ Dezon
    assert "vua thầu khái toán" in text


def test_report_numbers_trace_to_display_or_formatting():
    """Không có con số nào trong file do bộ dựng PDF tự nghĩ ra: mọi chuỗi chứa chữ số là display của engine,
    kết quả formatting trên đầu vào, hoặc dòng ngày/mã/phiên bản."""
    est = _estimate()
    rep = pdf.build_report(est, _matrix(), EXPORTED)
    allowed = {est.rate.display, est.cost.display, format_m2(est.converted_area_m2), *est.assumptions,
               *est.exclusions, *est.cost_drivers, *est.warnings, est.disclaimer, *(it.label_vi for it in est.items)}
    allowed |= {c.display for r in _matrix().rows for c in r.cells} | {c.name for c in _matrix().contractors}
    for it in rep.items:
        allowed |= {it.area, it.coefficient, it.converted}
    for t in pdf.report_texts(rep):
        if not re.search(r"\d", t) or t in allowed:
            continue
        assert ("11/11/2026" in t or "Hệ số khái toán phiên bản" in t or "cập nhật" in t or t.endswith(pdf.STALE_MARK)
                or t in {"AB12CD34", "100 m²", "4", "01/03/2026", "31/10/2026", "So sánh 3 nhà thầu"}
                or t.startswith("https://") or "AB12CD34" in t), t


# ------------------------------------------------------------------ dàn trang ReportLab
def test_markup_escapes_and_normalizes():
    nfd = unicodedata.normalize("NFD", "Khái toán")
    assert pdf.markup(nfd) == "Khái toán" and unicodedata.is_normalized("NFC", pdf.markup(nfd))
    assert pdf.markup("A & B <b>x</b>") == "A &amp; B &lt;b&gt;x&lt;/b&gt;"
    assert pdf._link('https://x.vn/"onclick', "Tên") == '<a href="https://x.vn/&quot;onclick" color="#0b5cad">Tên</a>'
    assert pdf._link("javascript:alert(1)", "Tên") == "Tên"


def test_font_covers_every_character():
    pdf.register_fonts()
    from reportlab.pdfbase import pdfmetrics

    rep = pdf.build_report(_estimate(basement=True, has_mezzanine=True), _matrix(5), EXPORTED)
    chars = {ch for t in pdf.report_texts(rep) for ch in unicodedata.normalize("NFC", t)} | set("•×²")
    chars |= set("ăâđêôơưĂÂĐÊÔƠƯáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
    for name in (pdf.FONT, pdf.FONT_BOLD):
        cmap = pdfmetrics.getFont(name).face.charToGlyph
        missing = sorted(ch for ch in chars if ch.strip() and ord(ch) not in cmap)
        assert not missing, (name, missing)


def test_render_pdf_bytes_portrait_and_deterministic():
    rep = pdf.build_report(_estimate(), _matrix(3), EXPORTED)
    out = pdf.render_pdf(rep)
    assert out.startswith(b"%PDF-") and out.rstrip().endswith(b"%%EOF")
    assert b"BeVietnamPro" in out
    assert b"/MediaBox [ 0 0 595.2756 841.8898 ]" in out
    assert pdf.render_pdf(rep) == out  # invariant=1


def test_render_pdf_landscape_for_more_than_three_contractors():
    out = pdf.render_pdf(pdf.build_report(_estimate(), _matrix(5), EXPORTED))
    assert b"/MediaBox [ 0 0 841.8898 595.2756 ]" in out  # trang so sánh nằm ngang
    assert b"/MediaBox [ 0 0 595.2756 841.8898 ]" in out


def test_render_pdf_without_rate():
    out = pdf.render_pdf(pdf.build_report(_estimate(rate=False), None, EXPORTED))
    assert out.startswith(b"%PDF-")


def test_pdf_filename():
    assert export_service.pdf_filename(SID, EXPORTED, False) == "khai-toan-vuathau-20261111-ab12cd34.pdf"
    assert export_service.pdf_filename(SID, datetime(2026, 11, 10, 18, 0, tzinfo=UTC), True) \
        == "khai-toan-vuathau-20261111-ab12cd34-so-sanh.pdf"  # 01:00 ngày 11 giờ Việt Nam


# ------------------------------------------------------------------ ComparisonTableBlock.estimate_session_id
def test_comparison_block_carries_estimate_session_id():
    out = blocks.build_ui_blocks([tools.ToolOutcome("compare_contractors", {}, _matrix())], estimate_session_id=SID)
    table = next(b for b in out if b.type == "comparison_table")
    assert table.estimate_session_id == SID
    out = blocks.build_ui_blocks([tools.ToolOutcome("compare_contractors", {}, _matrix())])
    assert next(b for b in out if b.type == "comparison_table").estimate_session_id is None


# ------------------------------------------------------------------ API
ESTIMATE = {"building_type": "nha_pho", "province": "TP. Hồ Chí Minh", "land_area_m2": 100, "floors": 4,
            "foundation_type": "foundation_pile", "roof_type": "roof_concrete", "work_scope": "xay_tho"}


@pytest.fixture
def pdf_client(client):
    from app.main import app
    from app.services import chat_service
    from app.services.chat_service import MemoryRateLimiter

    app.dependency_overrides[chat_service.get_pdf_rate_limiter] = lambda: MemoryRateLimiter(10_000)
    yield client
    app.dependency_overrides.pop(chat_service.get_pdf_rate_limiter, None)


def _ids(db, n=3):
    return [c.id for c in db.scalars(select(Contractor).order_by(Contractor.id)).all()][:n]


def _est_id(client):
    r = client.post("/api/estimates", json=ESTIMATE)
    assert r.status_code == 200, r.text
    return r.json()["estimate_session_id"]


def test_api_pdf_estimate_only(pdf_client, db):
    est_id = _est_id(pdf_client)
    r = pdf_client.get(f"/api/estimates/{est_id}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "no-store"
    cd = r.headers["content-disposition"]
    assert re.fullmatch(rf'attachment; filename="khai-toan-vuathau-\d{{8}}-{est_id.replace("-", "")[:8]}\.pdf"', cd), cd
    assert r.content.startswith(b"%PDF-")
    ev = db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.event == "estimate_pdf_exported")).all()
    assert any(e.props.get("estimate_session_id") == est_id and e.props.get("contractor_count") == 0 for e in ev)


def test_api_pdf_with_comparison(pdf_client, db):
    est_id = _est_id(pdf_client)
    ids = _ids(db, 3)
    r = pdf_client.get(f"/api/estimates/{est_id}/pdf", params={"contractor_ids": ids})
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"].endswith('-so-sanh.pdf"')
    ev = db.scalars(select(AnalyticsEvent).where(AnalyticsEvent.event == "estimate_pdf_exported")).all()
    assert any(e.props.get("contractor_count") == 3 for e in ev)


def test_api_pdf_not_found(pdf_client):
    assert pdf_client.get(f"/api/estimates/{uuid.uuid4()}/pdf").status_code == 404
    r = pdf_client.get("/api/estimates/abc/pdf")
    assert r.status_code == 404 and r.json()["detail"] == "Không tìm thấy phiên khái toán."


def test_api_pdf_invalid_contractor_ids(pdf_client, db):
    est_id = _est_id(pdf_client)
    ids = _ids(db, 3)
    url = f"/api/estimates/{est_id}/pdf"
    for bad in ([ids[0]], [1, 2, 3, 4, 5, 6], [0, ids[0]], [ids[0], 2**63]):
        r = pdf_client.get(url, params={"contractor_ids": bad})
        assert r.status_code == 422, (bad, r.text)
    r = pdf_client.get(url, params={"contractor_ids": [ids[0], ids[0]]})
    assert r.status_code == 422 and "trùng" in r.json()["detail"]
    r = pdf_client.get(url, params={"contractor_ids": [ids[0], 999_999_999]})
    assert r.status_code == 422 and "Không tìm thấy nhà thầu" in r.json()["detail"]
    r = pdf_client.get(url, params={"contractor_ids": "abc"})
    assert r.status_code == 422


def test_api_pdf_rate_limited(client):
    from app.main import app
    from app.services import chat_service
    from app.services.chat_service import MemoryRateLimiter

    limiter = MemoryRateLimiter(1)
    app.dependency_overrides[chat_service.get_pdf_rate_limiter] = lambda: limiter
    try:
        est_id = _est_id(client)
        assert client.get(f"/api/estimates/{est_id}/pdf").status_code == 200
        r = client.get(f"/api/estimates/{est_id}/pdf")
        assert r.status_code == 429 and r.headers["retry-after"]
        assert "quá nhiều" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(chat_service.get_pdf_rate_limiter, None)


# ------------------------------------------------------------------ ô so sánh rất dài (warranty_text thô)
LONG_WARRANTY = ("Bảo hành kết cấu 10 năm, chống thấm 5 năm, điện nước 2 năm, hoàn thiện 1 năm kể từ ngày bàn giao; "
                 "bảo trì định kỳ miễn phí trong năm đầu tiên. ") * 15  # ~2.000 ký tự


def _long_matrix(n: int) -> ComparisonMatrix:
    m = _matrix(n)
    rows = [*m.rows[:2], ComparisonRow(key="warranty", label_vi="Bảo hành", cells=[_ev(LONG_WARRANTY)] * n)]
    return m.model_copy(update={"rows": rows})


@pytest.mark.parametrize("n", [2, 3, 5])
def test_render_pdf_long_comparison_cells_do_not_overflow_page(n):
    rep = pdf.build_report(_estimate(), _long_matrix(n), EXPORTED)
    cell = rep.comparison.rows[-1].cells[0]
    assert len(cell) <= pdf.MAX_CELL_CHARS + 1 and cell.endswith("…")
    assert cell.startswith("Bảo hành kết cấu 10 năm")
    assert any("rút gọn" in note and "dezon.vn" in note for note in rep.comparison.notes)
    assert pdf.render_pdf(rep).startswith(b"%PDF-")  # trước đây: reportlab LayoutError -> HTTP 500


def test_short_comparison_cells_are_not_truncated():
    rep = pdf.build_report(_estimate(), _matrix(3), EXPORTED)
    assert not any("rút gọn" in note for note in rep.comparison.notes)
    assert pdf.truncate_cell("Bảo hành 5 năm") == "Bảo hành 5 năm"
    out = pdf.truncate_cell("chữ " * 200, limit=50)
    assert len(out) <= 51 and out.endswith("…") and not out.endswith(" …")


def test_render_pdf_long_contractor_name_does_not_overflow_page():
    m = _matrix(5, name0="Công ty TNHH Xây dựng " * 40)
    rep = pdf.build_report(_estimate(), m, EXPORTED)
    assert len(rep.comparison.contractors[0].name) <= pdf.MAX_NAME_CHARS + 1
    assert pdf.render_pdf(rep).startswith(b"%PDF-")


# ------------------------------------------------------------------ lỗi khi trình duyệt mở link tải PDF
BROWSER = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def _assert_html_error(r, status: int, message: str):
    assert r.status_code == status, r.text
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    assert r.headers["content-disposition"] == "inline"
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "Chưa tải được file PDF" in r.text and message in r.text
    assert 'href="https://pro.vuathau.com.vn"' in r.text and "Vua thầu khái toán" in r.text
    assert "tốt nhất" not in r.text


def test_api_pdf_errors_are_html_pages_for_browser_navigation(pdf_client, db):
    est_id = _est_id(pdf_client)
    url = f"/api/estimates/{est_id}/pdf"
    _assert_html_error(pdf_client.get(f"/api/estimates/{uuid.uuid4()}/pdf", headers=BROWSER), 404,
                       "Không tìm thấy phiên khái toán.")
    ids = _ids(db, 1)
    r = pdf_client.get(url, params={"contractor_ids": [ids[0], 999_999_999]}, headers=BROWSER)
    _assert_html_error(r, 422, "Không tìm thấy nhà thầu")
    assert "Thử lại" not in r.text  # lỗi dữ liệu: thử lại vô ích
    _assert_html_error(pdf_client.get(url, params={"contractor_ids": [ids[0]]}, headers=BROWSER), 422,
                       "Cần 0 hoặc từ 2 đến 5")
    # lỗi validation của FastAPI (VietnameseRoute trả JSON) cũng thành trang HTML
    _assert_html_error(pdf_client.get(url, params={"contractor_ids": "abc"}, headers=BROWSER), 422,
                       "Dữ liệu không hợp lệ")
    # thành công vẫn là PDF tải về
    r = pdf_client.get(url, headers=BROWSER)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"


def test_api_pdf_error_page_escapes_message(pdf_client, monkeypatch):
    def boom(*_a, **_k):
        raise ValueError('Không tìm thấy nhà thầu: <script>alert("x")</script>')

    monkeypatch.setattr(export_service, "estimate_pdf", boom)
    r = pdf_client.get(f"/api/estimates/{uuid.uuid4()}/pdf?x=\"><b>", headers=BROWSER)
    assert r.status_code == 422
    assert "<script>" not in r.text and "&lt;script&gt;" in r.text
    assert '"><b>' not in r.text


def test_api_pdf_rate_limited_html_page(client):
    from app.main import app
    from app.services import chat_service
    from app.services.chat_service import MemoryRateLimiter

    limiter = MemoryRateLimiter(1)
    app.dependency_overrides[chat_service.get_pdf_rate_limiter] = lambda: limiter
    try:
        est_id = _est_id(client)
        assert client.get(f"/api/estimates/{est_id}/pdf", headers=BROWSER).status_code == 200
        r = client.get(f"/api/estimates/{est_id}/pdf", headers=BROWSER)
        _assert_html_error(r, 429, "quá nhiều lần")
        assert r.headers["retry-after"]
        assert f'href="/api/estimates/{est_id}/pdf"' in r.text and "Thử lại" in r.text
    finally:
        app.dependency_overrides.pop(chat_service.get_pdf_rate_limiter, None)


def test_api_pdf_layout_error_returns_vietnamese_message(pdf_client, monkeypatch):
    from reportlab.platypus.doctemplate import LayoutError

    def too_large(_report):
        raise LayoutError("Flowable <Table> too large on page 2")

    monkeypatch.setattr(export_service, "render_pdf", too_large)
    est_id = _est_id(pdf_client)
    r = pdf_client.get(f"/api/estimates/{est_id}/pdf")
    assert r.status_code == 500 and "Em chưa tạo được file PDF" in r.json()["detail"]
    _assert_html_error(pdf_client.get(f"/api/estimates/{est_id}/pdf", headers=BROWSER), 500,
                       "Em chưa tạo được file PDF")


def test_api_pdf_unexpected_error_html_page_for_browser(pdf_client, monkeypatch):
    def crash(*_a, **_k):
        raise RuntimeError("bất ngờ")

    monkeypatch.setattr(export_service, "estimate_pdf", crash)
    _assert_html_error(pdf_client.get(f"/api/estimates/{uuid.uuid4()}/pdf", headers=BROWSER), 500,
                       "Em chưa tạo được file PDF lúc này")
    with pytest.raises(RuntimeError):  # client API: lỗi 500 như cũ (TestClient ném lại)
        pdf_client.get(f"/api/estimates/{uuid.uuid4()}/pdf")


def test_api_pdf_long_warranty_five_contractors(pdf_client, db):
    for i in range(5 - len(_ids(db, 5))):  # seed có thể ít hơn 5 nhà thầu
        db.add(Contractor(slug=f"nt-dai-{i}", name=f"Nhà thầu bảo hành dài {i}", profile_url=f"https://dezon.vn/nt-dai-{i}"))
    db.flush()
    ids = _ids(db, 5)
    for cid in ids:
        c = db.get(Contractor, cid)
        c.has_warranty_info, c.warranty_text = True, LONG_WARRANTY
    db.commit()
    est_id = _est_id(pdf_client)
    r = pdf_client.get(f"/api/estimates/{est_id}/pdf", params={"contractor_ids": ids})
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"%PDF-")
