"""Hồi quy phát hiện ở bước tích hợp (rebuild stack e2e với snapshot dữ liệu thật dezon.vn)."""
from app.ai import validator
from app.ai.fallback import build_fallback_text
from app.ai.tools import compact_for_llm


def _project(i: int) -> dict:
    return {"id": i, "name": f"Biệt thự {i}", "building_type": "biet_thu",
            "project_url": f"https://dezon.vn/du-an/biet-thu-{i}"}


def _overview_result() -> dict:
    """Câu hỏi tổng quan: nhà thầu A có 4 công trình biệt thự (nhiều hơn giới hạn 3 mục khi rút gọn cho LLM)."""
    def entry(cid: int, name: str, n: int) -> dict:
        return {"card": {"id": cid, "name": name, "slug": f"c{cid}"}, "has_projects_of_type": n > 0,
                "projects_of_type": [_project(cid * 10 + k) for k in range(n)], "projects_of_type_count": n}
    return {"province": "TP. Hồ Chí Minh", "building_type": "biet_thu",
            "contractor_count": {"value": 3, "display": "3 nhà thầu", "status": "on_dezon"},
            "contractors": [entry(1, "Kiến Phong", 4), entry(2, "Smartcons Tech", 3), entry(3, "Huy Hoàng", 0)]}


def test_overview_fallback_counts_per_contractor_are_traceable():
    """Trước đây "Kiến Phong (4 công trình biệt thự có trang dự án)" bị validator coi là số không truy được
    (danh sách projects_of_type không được đếm) -> lượt trả lời rơi về câu xin lỗi chung dù bảng vẫn hiện."""
    result = _overview_result()
    text = build_fallback_text([("get_contractor_overview", result)])
    assert "4 công trình biệt thự" in text and "3 công trình biệt thự" in text
    for tool_results in ([("get_contractor_overview", result)],
                         [("get_contractor_overview", compact_for_llm(result))]):
        res = validator.check(text, tool_results)
        assert res.ok, res.violations


def test_overview_compact_keeps_full_count_for_llm():
    compact = compact_for_llm(_overview_result())
    kp = compact["contractors"][0]
    assert kp["projects_of_type_count"] == 4
    assert len([p for p in kp["projects_of_type"] if "id" in p]) == 3  # danh sách vẫn bị cắt cho gọn
