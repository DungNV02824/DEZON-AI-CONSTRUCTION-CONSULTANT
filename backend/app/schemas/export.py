"""Mô hình nội dung file PDF khái toán (+ so sánh nhà thầu) — V2 xuất PDF (đặc tả mục 15).

Mọi chuỗi ở đây đã là chuỗi hiển thị cuối cùng: lấy nguyên `EvidenceValue.display` hoặc kết quả của các hàm
trong `app/formatting.py`. Bộ dựng PDF (`app/export/estimate_pdf.py`) chỉ dàn trang, không tự định dạng số.
"""
from pydantic import BaseModel


class ReportRow(BaseModel):
    label: str
    value: str
    note: str | None = None  # dòng phụ, vd "Nguồn: … · cập nhật dd/mm/yyyy"
    emphasize: bool = False


class ReportItemRow(BaseModel):
    """Một dòng bảng hệ số: Hạng mục | A_i (m²) | k_i | A_i×k_i."""
    label: str
    area: str
    coefficient: str
    converted: str


class ReportList(BaseModel):
    title: str
    items: list[str]
    warn: bool = False


class ReportSource(BaseModel):
    label: str
    url: str | None = None
    updated_at: str  # dd/mm/yyyy hoặc "Chưa có dữ liệu"
    is_stale: bool = False


class ReportContractor(BaseModel):
    name: str
    profile_url: str | None = None


class ReportComparisonRow(BaseModel):
    label: str
    cells: list[str]  # cùng thứ tự với ReportComparison.contractors; ô thiếu = "Chưa có dữ liệu"


class ReportComparison(BaseModel):
    title: str  # vd "So sánh 3 nhà thầu"
    contractors: list[ReportContractor]
    rows: list[ReportComparisonRow]
    notes: list[str] = []  # vd "Dữ liệu tại thời điểm xuất dd/mm/yyyy HH:mm…"


class EstimateReport(BaseModel):
    brand_name: str
    site_url: str
    site_label: str
    generated_at: str  # "Ngày xuất dd/mm/yyyy HH:mm"
    estimate_code: str  # 8 ký tự đầu của estimate_session_id (in hoa)
    title: str
    subtitle: str | None = None
    project_rows: list[ReportRow]
    estimate_rows: list[ReportRow]
    items_title: str
    items: list[ReportItemRow]
    items_total: str
    lists: list[ReportList] = []
    disclaimer: str
    comparison: ReportComparison | None = None
    sources: list[ReportSource] = []
    notes: list[str] = []  # ghi chú cuối file
