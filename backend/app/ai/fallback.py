"""Câu trả lời dự phòng (đặc tả mục 2.1).

Dùng khi LLM sinh câu trả lời vi phạm guardrail hai lần liên tiếp. Văn bản CHỈ dựng từ dữ liệu tool
bằng template: dùng nguyên các chuỗi `display` / văn bản do engine trả về, không tự định dạng số mới,
nên luôn qua được `validator.check`.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.formatting import NO_DATA
from app.schemas.common import BUILDING_TYPE_VI, QUALITY_TIER_VI, WORK_SCOPE_VI

log = logging.getLogger(__name__)

GENERIC_APOLOGY = (
    "Dạ, em xin lỗi, hiện em chưa thể đưa ra câu trả lời chính xác cho nội dung này. "
    "Anh/chị có thể mô tả thêm nhu cầu xây dựng (loại công trình, khu vực, diện tích đất, số tầng, "
    "phạm vi thi công) để em hỗ trợ tiếp được không ạ?"
)
ESTIMATE_NOTE = "Đây là khái toán sơ bộ, không phải báo giá thi công."
PROJECT_COUNT_NOTE = "Số công trình là số công trình ghi nhận trên Dezon, không đồng nghĩa với số công trình đã hoàn thành."

SLOT_LABEL_VI: dict[str, str] = {
    "building_type": "loại công trình", "province": "tỉnh/thành phố", "district": "quận/huyện",
    "land_area_m2": "diện tích đất (hoặc chiều rộng × chiều dài)", "land_width_m": "chiều rộng đất",
    "land_length_m": "chiều dài đất", "floors": "số tầng", "work_scope": "phạm vi thi công",
    "quality_tier": "mức hoàn thiện", "budget_vnd": "ngân sách dự kiến", "start_time": "thời gian dự kiến khởi công",
}
INFO_LABEL_VI: dict[str, str] = {
    "province": "Khu vực", "founded_year": "Năm thành lập", "years_experience": "Kinh nghiệm",
    "staff": "Nhân sự", "staff_workers": "Nhân sự (thợ)", "staff_office": "Nhân sự (văn phòng)",
    "rough_price": "Đơn giá xây thô", "finish_price": "Đơn giá hoàn thiện cơ bản",
    "typical_project_value": "Quy mô dự án", "dezon_project_count": "Công trình ghi nhận trên Dezon",
    "warranty": "Bảo hành",
}
PROFILE_INFO_ORDER = ["founded_year", "years_experience", "staff_workers", "staff_office", "staff", "rough_price",
                      "finish_price", "typical_project_value", "dezon_project_count", "warranty"]
MAX_ITEMS = 5


# --------------------------------------------------------------------------------------------------
# Tiện ích
# --------------------------------------------------------------------------------------------------
def _j(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, (list, tuple)):
        return [_j(x) for x in obj]
    return obj


def _disp(ev: Any) -> str:
    """Chuỗi display của một EvidenceValue (dict); thiếu -> "Chưa có dữ liệu"."""
    ev = _j(ev)
    if isinstance(ev, dict):
        d = ev.get("display")
        if ev.get("status") == "unavailable" or ev.get("value") is None or not d:
            return NO_DATA
        return str(d)
    return NO_DATA


def _has(ev: Any) -> bool:
    return _disp(ev) != NO_DATA


def _source_note(ev: Any) -> str:
    """"(nguồn: X, cập nhật dd/mm/yyyy)" từ EvidenceValue; cảnh báo nếu is_stale."""
    ev = _j(ev)
    if not isinstance(ev, dict) or not isinstance(ev.get("source"), dict):
        return ""
    src = ev["source"]
    parts = []
    if src.get("label"):
        parts.append(f"nguồn: {src['label']}")
    upd = src.get("updated_at")
    if upd and isinstance(upd, str):
        ymd = upd[:10].split("-")
        if len(ymd) == 3 and all(x.isdigit() for x in ymd):
            parts.append(f"cập nhật {ymd[2]}/{ymd[1]}/{ymd[0]}")
    note = f" ({', '.join(parts)})" if parts else ""
    if ev.get("is_stale"):
        note += " — dữ liệu có thể đã cũ"
    return note


def _vi(mapping: dict[str, str], key: Any) -> str:
    return mapping.get(str(key), str(key)) if key is not None else ""


# --------------------------------------------------------------------------------------------------
# Template từng tool
# --------------------------------------------------------------------------------------------------
def _requirements(r: dict) -> str:
    slots = r.get("slots") if isinstance(r.get("slots"), dict) else r
    missing = r.get("missing_required") or []
    known = []
    if slots.get("building_type"):
        known.append(_vi(BUILDING_TYPE_VI, slots["building_type"]).lower())
    if slots.get("province"):
        known.append(f"tại {slots['province']}")
    if slots.get("work_scope"):
        known.append(f"phạm vi {_vi(WORK_SCOPE_VI, slots['work_scope']).lower()}")
    head = "Dạ, em đã ghi nhận thông tin" + (f" công trình {' '.join(known)}" if known else "") + "."
    if missing:
        labels = [SLOT_LABEL_VI.get(k, k) for k in missing]
        return f"{head} Để tính khái toán sơ bộ, anh/chị cho em biết thêm: {', '.join(labels)} ạ."
    return f"{head} Em đã có đủ thông tin cần thiết; anh/chị xác nhận để em tính khái toán sơ bộ nhé."


def _estimate(r: dict) -> str:
    inp = r.get("input") or {}
    what = " ".join(x for x in [
        _vi(BUILDING_TYPE_VI, inp.get("building_type")).lower(),
        f"tại {inp['province']}" if inp.get("province") else "",
        f"— {_vi(WORK_SCOPE_VI, inp.get('work_scope')).lower()}" if inp.get("work_scope") else "",
        f"mức {_vi(QUALITY_TIER_VI, inp.get('quality_tier')).lower()}" if inp.get("quality_tier") else "",
    ] if x)
    lines = [f"Dạ, em gửi anh/chị khái toán sơ bộ cho công trình {what}:".replace("  ", " ")]
    if _has(r.get("cost")):
        lines.append(f"- Chi phí sơ bộ: {_disp(r.get('cost'))}.")
    else:
        lines.append(f"- Chi phí sơ bộ: {NO_DATA} (hiện chưa có đơn giá tham chiếu phù hợp).")
    lines.append(f"- Đơn giá tham chiếu: {_disp(r.get('rate'))}{_source_note(r.get('rate'))}.")
    exclusions = r.get("exclusions") or []
    if exclusions:
        lines.append(f"- Chưa bao gồm: {'; '.join(str(x) for x in exclusions[:MAX_ITEMS])}.")
    drivers = r.get("cost_drivers") or []
    if drivers:
        lines.append(f"- Yếu tố có thể làm tăng chi phí: {'; '.join(str(x) for x in drivers[:MAX_ITEMS])}.")
    for w in (r.get("warnings") or [])[:MAX_ITEMS]:
        lines.append(f"- Lưu ý: {w}")
    lines.append(f"{ESTIMATE_NOTE} Chi tiết hệ số, giả định và nguồn đơn giá có trong bảng khái toán; "
                 "anh/chị nên yêu cầu báo giá từ nhà thầu để có con số sát thực tế.")
    return "\n".join(lines)


def _budget(r: dict) -> str:
    lines = [f"Dạ, với ngân sách anh/chị đưa ra là {_disp(r.get('target_budget'))}, khái toán hiện tại là "
             f"{_disp(r.get('current_cost'))}. Các phương án điều chỉnh do hệ thống tính:"]
    options = r.get("options") or []
    if not options:
        lines.append("- Hiện chưa có phương án điều chỉnh phù hợp.")
    for o in options:
        fit = " — nằm trong ngân sách" if o.get("fits_budget") else ""
        desc = f" ({o['description']})" if o.get("description") else ""
        lines.append(f"- {o.get('label_vi', '')}{desc}: {_disp(o.get('cost'))}{fit}.")
    lines.append(ESTIMATE_NOTE)
    return "\n".join(lines)


def _match(r: dict) -> str:
    results = r.get("results") or []
    if not results:
        notes = " ".join(str(n) for n in (r.get("notes") or []))
        return ("Dạ, hiện em chưa tìm thấy nhà thầu phù hợp với tiêu chí của anh/chị trên Dezon. "
                + (notes + " " if notes else "")
                + "Anh/chị có thể nới rộng khu vực hoặc điều chỉnh tiêu chí để em tìm lại ạ.")
    names = [str(m.get("contractor", {}).get("name", "")) for m in results]
    lines = [f"Dạ, em tìm được {len(results)} nhà thầu phù hợp với tiêu chí của anh/chị: {', '.join(names)}."]
    if r.get("relaxed_to_neighbors"):
        lines.append("Một số nhà thầu được đề xuất từ tỉnh lân cận do khu vực của anh/chị còn ít lựa chọn.")
    mentions_projects = False
    for m in results:
        c = m.get("contractor", {})
        reasons = [str(x.get("text", "")) for x in (m.get("reasons") or []) if x.get("text")]
        cautions = [str(x.get("text", "")) for x in (m.get("cautions") or []) if x.get("text")]
        mentions_projects = mentions_projects or any("công trình" in t or "dự án" in t for t in reasons + cautions)
        line = f"- {c.get('name', '')} ({m.get('fit_label', '')})"
        if reasons:
            line += f": {'; '.join(reasons)}"
        if cautions:
            line += f". Cần lưu ý: {'; '.join(cautions)}"
        if c.get("profile_url"):
            line += f". Hồ sơ: {c['profile_url']}"
        lines.append(line + ".")
    for n in r.get("notes") or []:
        lines.append(str(n))
    if mentions_projects:
        lines.append(PROJECT_COUNT_NOTE)
    lines.append("Thứ tự trên là mức phù hợp với tiêu chí của anh/chị, không phải xếp hạng chất lượng thi công. "
                 "Anh/chị có thể xem hồ sơ, so sánh hoặc gửi yêu cầu báo giá ạ.")
    return "\n".join(lines)


def _profile(r: dict) -> str:
    card = r.get("card") or {}
    ev = card.get("evidence") or {}
    info = dict(ev)
    info.update(r.get("info") or {})
    head = f"Dạ, đây là thông tin hồ sơ {card.get('name', 'nhà thầu')}"
    if card.get("badge"):
        head += f" ({card['badge']})"
    head += f" trên Dezon — khu vực: {_disp(ev.get('province'))}."
    lines = [head]
    if "staff_workers" in info or "staff_office" in info:
        info.pop("staff", None)  # tránh lặp dòng nhân sự
    for key in PROFILE_INFO_ORDER:
        if key in info:
            lines.append(f"- {INFO_LABEL_VI[key]}: {_disp(info[key])}{_source_note(info[key])}.")
    projects = r.get("projects") or []
    if projects:
        lines.append("Một số công trình ghi nhận trên Dezon:")
        for p in projects[:MAX_ITEMS]:
            loc = f" — {p['province']}" if p.get("province") else ""
            url = f" ({p['project_url']})" if p.get("project_url") else ""
            lines.append(f"- {p.get('name', '')}{loc}{url}")
    if "dezon_project_count" in info or projects:
        lines.append(PROJECT_COUNT_NOTE)
    if card.get("profile_url"):
        lines.append(f"Anh/chị có thể mở hồ sơ đầy đủ trên Dezon: {card['profile_url']}")
    return "\n".join(lines)


def _compare(r: dict) -> str:
    contractors = r.get("contractors") or []
    names = [str(c.get("name", "")) for c in contractors]
    lines = [f"Dạ, em đã lập bảng so sánh {len(contractors)} nhà thầu: {', '.join(names)}."]
    mentions_projects = False
    for row in r.get("rows") or []:
        cells = row.get("cells") or []
        vals = [f"{names[i] if i < len(names) else ''}: {_disp(cell)}" for i, cell in enumerate(cells)]
        lines.append(f"- {row.get('label_vi', row.get('key', ''))}: {'; '.join(vals)}.")
        mentions_projects = mentions_projects or row.get("key") in ("dezon_project_count", "projects")
    lines.append(f"Ô \"{NO_DATA}\" nghĩa là Dezon chưa có thông tin cho tiêu chí đó.")
    if mentions_projects:
        lines.append(PROJECT_COUNT_NOTE)
    return "\n".join(lines)


def _rates(r: Any) -> str:
    if isinstance(r, list):  # service trả tuple (EvidenceValue, RateData | None)
        r = r[0] if r else {}
    rate = r.get("rate", r) if isinstance(r, dict) else {}
    if not _has(rate):
        return (f"Dạ, hiện em chưa có đơn giá tham chiếu cho loại công trình và khu vực này ({NO_DATA}). Anh/chị có "
                "thể gửi yêu cầu báo giá để nhà thầu báo đơn giá cụ thể ạ.")
    return (f"Dạ, đơn giá tham chiếu hiện có là {_disp(rate)}{_source_note(rate)}. "
            "Đây là đơn giá tham khảo để khái toán sơ bộ, không phải báo giá thi công.")


def _knowledge(r: Any) -> str:
    from app.ai.validator import extract_claims  # import muộn: validator import fallback

    if isinstance(r, dict):
        chunks = r.get("chunks") or r.get("results") or r.get("items") or []
    else:
        chunks = r if isinstance(r, list) else []
    lines: list[str] = []
    for ch in chunks[:3]:
        ch = _j(ch)
        if not isinstance(ch, dict):
            continue
        content = str(ch.get("content") or ch.get("text") or "")
        # RAG chỉ dùng cho mô tả: bỏ mọi câu có số liệu.
        sentences = [s.strip() for s in content.replace("\n", " ").split(". ") if s.strip()]
        clean = [s for s in sentences if not extract_claims(s)]
        snippet = ". ".join(clean)
        if len(snippet) > 300:
            snippet = snippet[:300].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
        snippet = snippet.rstrip()
        src = ch.get("source") if isinstance(ch.get("source"), dict) else {}
        label = src.get("label") or ch.get("title") or ch.get("entity_name") or ""
        if label and extract_claims(str(label)):
            label = ""
        url = src.get("url") or ch.get("url") or ""
        if snippet:
            lines.append(f"- {snippet}" + (f" (nguồn: {label}{', ' + url if url else ''})" if label or url else ""))
    if not lines:
        return ""
    return "Dạ, em tìm thấy một số thông tin mô tả liên quan trên Dezon:\n" + "\n".join(lines)


def _quote(r: dict) -> str:
    ids = r.get("ids") or []
    names = [str(n) for n in (r.get("contractor_names") or []) if n]
    code = r.get("code")
    msg = "Dạ, em đã ghi nhận yêu cầu báo giá của anh/chị"
    if names:
        msg += f" gửi tới {', '.join(names)}"
    elif ids:
        msg += f" gửi tới {len(ids)} nhà thầu"
    msg += "."
    if code:
        msg += f" Mã yêu cầu: {code}."
    return msg + " Nhà thầu sẽ liên hệ lại để khảo sát và gửi báo giá chi tiết cho anh/chị."


def _projects(r: Any) -> str:
    """get_contractor_projects: nêu TỔNG số công trình ghi nhận trên Dezon (nếu có) tách khỏi số trang dự án, để khách
    không hiểu nhầm số trang dự án là số công trình; danh sách/link nằm trong thẻ hồ sơ."""
    if not isinstance(r, dict):
        items = _j(r) if isinstance(r, list) else []
        if not items:
            return f"Dạ, Dezon chưa có trang dự án nào của nhà thầu này ({NO_DATA})."
        names = [str(p.get("name", "")) for p in items[:MAX_ITEMS] if isinstance(p, dict) and p.get("name")]
        return "Dạ, một số công trình có trang dự án trên Dezon: " + ", ".join(names) + "."
    items = _j(r.get("projects") or r.get("items") or [])
    items = items if isinstance(items, list) else []
    name = str((r.get("contractor") or {}).get("name") or "nhà thầu này")
    bt = _vi(BUILDING_TYPE_VI, r.get("building_type")).lower() if r.get("building_type") else ""
    kind = f"công trình {bt}" if bt else "công trình"
    count = r.get("dezon_project_count")
    if _has(count):
        head = f"Dạ, {name} có {_disp(count)} (không đồng nghĩa với số công trình đã hoàn thành)"
    else:
        head = f"Dạ, hồ sơ {name} trên Dezon chưa có số công trình ghi nhận ({NO_DATA})"
    if items:
        return f"{head}; trong đó {len(items)} {kind} có trang dự án, anh/chị xem trong thẻ hồ sơ bên dưới."
    joiner = " nhưng" if _has(count) else ", và"
    return f"{head}{joiner} Dezon chưa có trang dự án {(bt + ' ') if bt else ''}nào của nhà thầu này."


def _overview(r: dict) -> str:
    """ContractorOverview (mục 8.4): câu ngắn — số nhà thầu, nhà thầu nào có công trình đúng loại (kèm số trang dự án đúng loại);
    chi tiết từng nhà thầu ở bảng (block comparison_table), nguồn ở chấm màu/badge — không lặp nguồn/URL trong câu."""
    bt = _vi(BUILDING_TYPE_VI, r.get("building_type")).lower()
    where = f" tại {r['province']}" if r.get("province") else ""
    entries = [e for e in (r.get("contractors") or []) if isinstance(e, dict)]
    if not entries:
        return (f"Dạ, hiện Dezon chưa ghi nhận nhà thầu {bt}{where} ({NO_DATA}). Anh/chị có thể thử khu vực lân cận "
                "hoặc để em tìm theo thông tin công trình của anh/chị ạ.")
    lines = [f"Dạ, trên Dezon có {_disp(r.get('contractor_count'))} phù hợp, nhận thi công {bt}{where}."]
    with_type = [e for e in entries if e.get("has_projects_of_type")]
    if with_type:
        parts = []
        for e in with_type[:MAX_ITEMS]:
            n = e.get("projects_of_type_count") or len(e.get("projects_of_type") or [])
            nm = str((e.get("card") or {}).get("name", ""))
            parts.append(f"{nm} ({n} công trình {bt} có trang dự án)" if n else nm)
        rest = (f"; các nhà thầu còn lại chưa có trang dự án {bt} trên Dezon" if len(with_type) < len(entries)
                else "")
        lines.append(f"Nhà thầu có công trình {bt} thực tế trên Dezon: {', '.join(parts)}{rest}.")
    else:
        lines.append(f"Chưa nhà thầu nào có trang dự án {bt} trên Dezon.")
    lines.append("Công trình ghi nhận, đơn giá tham khảo và bảo hành của từng nhà thầu có trong bảng bên dưới.")
    lines.append("Dezon chưa có dữ liệu đánh giá chất lượng thi công sau bàn giao nên em không xếp hạng nhà thầu; "
                 "anh/chị nên xem công trình thực tế và hồ sơ của từng đơn vị ạ.")
    return " ".join(lines)


_TEMPLATES: dict[str, Callable[[Any], str]] = {
    "get_contractor_overview": _overview,
    "update_project_requirements": _requirements,
    "calculate_estimate": _estimate,
    "suggest_budget_adjustments": _budget,
    "search_contractors": _match,
    "get_contractor_profile": _profile,
    "compare_contractors": _compare,
    "get_reference_rates": _rates,
    "search_knowledge": _knowledge,
    "create_quote_request": _quote,
    "get_contractor_projects": _projects,
}


def infer_tool_name(result: Any) -> str | None:
    """Suy ra tên tool từ kiểu/hình dạng kết quả (khi caller không truyền tên)."""
    from app.schemas.estimate import BudgetAdjustmentResult, EstimateResult
    from app.schemas.matching import ComparisonMatrix, ContractorProfile, MatchResult

    for cls, name in ((EstimateResult, "calculate_estimate"), (BudgetAdjustmentResult, "suggest_budget_adjustments"),
                      (MatchResult, "search_contractors"), (ContractorProfile, "get_contractor_profile"),
                      (ComparisonMatrix, "compare_contractors")):
        if isinstance(result, cls):
            return name
    r = _j(result)
    if isinstance(r, list):
        first = r[0] if r else None
        if isinstance(first, dict) and "project_url" in first:
            return "get_contractor_projects"
        if isinstance(first, dict) and "content" in first and ("usage_note" in first or "entity_type" in first):
            return "search_knowledge"  # list[KnowledgeChunk]
        if len(r) == 2 and isinstance(first, dict) and {"display", "status"} <= set(first) and (
                r[1] is None or (isinstance(r[1], dict) and "price_min" in r[1])):
            return "get_reference_rates"  # tuple (EvidenceValue, RateData | None) của estimate_service
        return None
    if not isinstance(r, dict):
        return None
    keys = set(r)
    if {"contractor_count", "contractors_with_projects"} <= keys:
        return "get_contractor_overview"
    if "missing_required" in keys:
        return "update_project_requirements"
    if {"items", "converted_area_m2", "cost"} <= keys:
        return "calculate_estimate"
    if {"options", "target_budget_vnd"} <= keys:
        return "suggest_budget_adjustments"
    if {"results", "weights_version"} <= keys:
        return "search_contractors"
    if {"card", "info"} <= keys:
        return "get_contractor_profile"
    if {"rows", "contractors"} <= keys:
        return "compare_contractors"
    if "rate" in keys and ("rate_data" in keys or len(keys) == 1):
        return "get_reference_rates"
    if "code" in keys and "ids" in keys:
        return "create_quote_request"
    if "chunks" in keys:
        return "search_knowledge"
    return None


def ensure_named(item: Any) -> tuple[str, Any]:
    """Chuẩn hóa phần tử tool_results về cặp (tool_name, result)."""
    if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
        return item
    return (infer_tool_name(item) or "", item)


def build_fallback_text(tool_results: list[tuple[str, Any]]) -> str:
    """Câu trả lời dự phòng chỉ dựng từ dữ liệu tool (template).

    `tool_results`: danh sách `(tool_name, result)`; result là pydantic hoặc dict JSON.
    Không có kết quả dùng được -> câu xin lỗi chung, không có số liệu.
    """
    parts: list[str] = []
    for item in tool_results or []:
        name, result = ensure_named(item)
        if result is None:
            continue
        tpl = _TEMPLATES.get(name) or _TEMPLATES.get(infer_tool_name(result) or "")
        if tpl is None:
            continue
        try:
            text = tpl(_j(result))
        except Exception:  # noqa: BLE001 — câu dự phòng không bao giờ được làm hỏng lượt chat
            log.warning("fallback template %s lỗi", name, exc_info=True)
            text = ""
        if text and text.strip():
            parts.append(text.strip())
    if not parts:
        return GENERIC_APOLOGY
    return "\n\n".join(parts)
