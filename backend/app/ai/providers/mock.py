"""MockProvider — "LLM" kịch bản, xác định, KHÔNG gọi mạng (demo toàn bộ luồng MVP khi chưa có API key).

Quyết định gọi tool dựa trên tin nhắn cuối + khối trạng thái `context` do orchestrator truyền (slots, stage, intent,
nhà thầu đã đề xuất). Văn bản viết bằng template CHỈ dùng chuỗi `display` trong kết quả tool:
- không bao giờ dùng "tốt nhất"; số dự án luôn là "công trình ghi nhận trên Dezon";
- khái toán luôn nhắc "không phải báo giá"; trường thiếu nói "Chưa có dữ liệu";
- khi đã có block (bảng khái toán, danh sách nhà thầu, phương án ngân sách, thẻ hồ sơ) văn bản chỉ là lời dẫn ngắn +
  giải thích, KHÔNG chép lại nội dung block (mục 6.5, nghiệm thu W-F7/C-F7).

`MockProvider(lie_text=...)`: chế độ "nói dối" cho test guardrail — luôn trả văn bản cố định (vd có số bịa).
`extract()` luôn trả {} (slot filling dùng bộ trích quy tắc).
"""
from __future__ import annotations

import json
import re
from collections import deque
from typing import Any

from app.ai import fallback
from app.ai.fallback import build_fallback_text
from app.ai.normalize import (
    fold,
    is_hypothetical,
    parse_building_type,
    parse_location,
    parse_message,
    parse_quality_tier,
    parse_work_scope,
)
from app.ai.providers.base import LLMResponse, Message, ToolCall, ToolSpec
from app.formatting import NO_DATA, format_m2
from app.schemas.common import QUALITY_TIER_VI, WORK_SCOPE_VI

ESTIMATE_NOTE = "Đây là khái toán sơ bộ, không phải báo giá thi công."
PROJECT_NOTE = ("Số công trình là số công trình ghi nhận trên Dezon, không đồng nghĩa với số công trình đã hoàn "
                "thành.")
NATIONAL_RATE_PREFIX = "Dùng đơn giá mặc định toàn quốc"

_OVERVIEW_RE = re.compile(r"tot nhat|uy tin|nen chon|dang tin|tot tai|tot o |thi cong .{0,40}\btot\b|nha thau nao")
_LIST_RE = re.compile(r"(tim|de xuat|goi y|gioi thieu|liet ke)\s+(cho\s+\w+\s+)?(cac\s+|mot so\s+|vai\s+)?nha thau")
_PRICE_RE = re.compile(r"\bgia\b|don gia|bao nhieu tien|chi phi")
_PROJECTS_RE = re.compile(r"\bdu an\b|\bcong trinh\b")
_PHONE_RE = re.compile(r"(?:\+?84|0)[\d\s.\-]{8,13}\d")
# "tên là X", "tên tôi là X", "tên X", "họ tên X", "tôi là X" (C-F2: "tên Nguyễn Văn A, sđt ..." cũng hiểu được).
_CUSTOMER_NAME_RE = re.compile(
    r"(?:\b(?:họ\s+(?:và\s+)?)?tên(?:\s+(?:tôi|em|mình|tui))?(?:\s*(?:là|:))?|\btôi\s+là|\bmình\s+là|\bem\s+là)"
    r"\s+([^\d,.;:!?]+?)(?=\s*(?:[,.;:!?]|\bsố\b|\bsđt\b|\bsdt\b|\bđiện\b|$))", re.IGNORECASE)
_NAME_TRIGGERS = ("của", "nhà thầu", "hồ sơ", "đơn vị", "công ty", "về")
_STOP_NAME_WORDS = {"TP.", "TP", "Tp", "Tp.", "Quận", "Huyện", "Thành", "Tỉnh"}
# Từ đầu câu viết hoa nhưng không phải tên nhà thầu ("Tôi Muốn...", "Nhà Thầu...", "Cho Em...").
_LEAD_STOP_WORDS = {"toi", "minh", "em", "anh", "chi", "cho", "nha", "xin", "co", "bao", "gia", "don", "neu", "khi",
                    "vay", "the", "lam", "xay", "tim", "so", "dezon", "cong", "ngan", "dat", "chao"}
# Câu hỏi về chi phí/giá — không trả lời bằng đoạn mô tả RAG (mô tả không phải nguồn số liệu).
_COST_Q_RE = re.compile(r"\bgia\b|don gia|bao nhieu tien|chi phi|het bao nhieu|ton bao nhieu|mat bao nhieu")
# Hạng mục chi phí NGOÀI phạm vi engine khái toán: (mẫu, tên hạng mục, yếu tố quyết định chi phí).
_OUT_OF_ENGINE: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b(?:ep|khoan|dong)\s+coc\b"), "ép cọc/khoan cọc",
     "kết quả khảo sát địa chất, số lượng và chiều dài cọc"),
    (re.compile(r"giay phep|xin phep"), "xin giấy phép xây dựng", "địa phương và hồ sơ thiết kế"),
    (re.compile(r"\bthiet ke\b"), "thiết kế kiến trúc, kết cấu", "đơn vị thiết kế và mức độ chi tiết của hồ sơ"),
    (re.compile(r"thao do|pha do|nha cu"), "tháo dỡ công trình cũ", "hiện trạng công trình cũ"),
    (re.compile(r"khao sat dia chat"), "khảo sát địa chất", "diện tích và điều kiện khu đất"),
    (re.compile(r"dau noi|dien nuoc ngoai nha"), "điện nước ngoài nhà, đấu nối hạ tầng", "hiện trạng hạ tầng khu vực"),
]
_GREETING_RE = re.compile(r"^(?:xin chao|chao|hello|hi|alo)\b")
_CHEAP_RE = re.compile(r"re nhat|gia re|re hon|gia thap|thap nhat")
# Câu hỏi về một mục cụ thể trong hồ sơ nhà thầu: (mẫu, khóa info, tên mục).
_PROFILE_TOPICS: list[tuple[re.Pattern[str], list[str], str]] = [
    (re.compile(r"kinh nghiem|bao nhieu nam|may nam|thanh lap|hoat dong (?:duoc )?bao lau"),
     ["years_experience", "founded_year"], "kinh nghiệm"),
    (re.compile(r"bao hanh"), ["warranty"], "bảo hành"),
    (re.compile(r"nhan su|bao nhieu nguoi|\btho\b|ky su|nhan vien"), ["staff_workers", "staff_office", "staff"],
     "nhân sự"),
    (re.compile(r"quy mo|gia tri (?:du an|cong trinh)"), ["typical_project_value"], "quy mô dự án"),
]
_ESTIMATE_OVERRIDE_KEYS = ("building_type", "province", "district", "land_width_m", "land_length_m", "land_area_m2",
                           "building_area_m2", "floors", "has_mezzanine", "basement", "basement_depth_m", "roof_type",
                           "rooftop_terrace", "foundation_type", "work_scope", "quality_tier")


def _ev(d: Any) -> dict:
    return d if isinstance(d, dict) else {}


def _disp(ev: Any) -> str:
    ev = _ev(ev)
    if not ev or ev.get("status") == "unavailable" or ev.get("value") is None or not ev.get("display"):
        return NO_DATA
    return str(ev["display"])


def _src(ev: Any) -> str:
    ev = _ev(ev)
    src = _ev(ev.get("source"))
    if not src or _disp(ev) == NO_DATA:
        return ""
    parts = []
    if src.get("label"):
        parts.append(f"nguồn: {src['label']}")
    upd = str(src.get("updated_at") or "")
    ymd = upd[:10].split("-")
    if len(ymd) == 3 and all(x.isdigit() for x in ymd):
        parts.append(f"cập nhật {ymd[2]}/{ymd[1]}/{ymd[0]}")
    note = f" ({', '.join(parts)})" if parts else ""
    if ev.get("is_stale"):
        note += " — dữ liệu có thể đã cũ"
    return note


def _lc_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _uc_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def contractor_name_in(message: str, known: list[dict] | None = None) -> str | None:
    """Tên nhà thầu khách nhắc tới: ưu tiên tên đã đề xuất trong phiên, sau đó cụm Viết Hoa sau "của/nhà thầu/..."."""
    f = fold(message)
    for c in known or []:
        name = str(c.get("name") or "")
        if name and fold(name) in f:
            return name
    tokens = message.replace("?", " ? ").replace(",", " , ").split()
    for i in range(len(tokens)):
        for trig in _NAME_TRIGGERS:
            words = trig.split()
            if [t.lower() for t in tokens[i:i + len(words)]] != words:
                continue
            name: list[str] = []
            for t in tokens[i + len(words):]:
                t2 = t.strip(".,;:!?\"'()")
                if not t2 or not t2[0].isupper() or t2 in _STOP_NAME_WORDS:
                    break
                name.append(t2)
                if len(name) >= 4:
                    break
            if name:
                return " ".join(name)
    # Câu mở đầu bằng tên riêng ≥ 2 từ Viết Hoa ("Kiến Phong có bao nhiêu công trình?") — không phải địa danh.
    lead: list[str] = []
    for t in tokens:
        t2 = t.strip(".,;:!?\"'()")
        if not t2 or not t2[0].isupper() or t2 in _STOP_NAME_WORDS or len(lead) >= 4:
            break
        lead.append(t2)
    if len(lead) >= 2 and fold(lead[0]) not in _LEAD_STOP_WORDS and not parse_location(" ".join(lead)):
        return " ".join(lead)
    return None


def out_of_engine_topic(f: str) -> tuple[str, str] | None:
    """Hạng mục chi phí ngoài phạm vi engine mà câu hỏi (đã gập dấu) nhắc tới: (tên hạng mục, yếu tố chi phí)."""
    for rx, label, reason in _OUT_OF_ENGINE:
        if rx.search(f):
            return label, reason
    return None


def hypothetical_overrides(message: str) -> dict[str, Any]:
    """Tham số giả định cho calculate_estimate từ câu "nếu ... thì ..." (chỉ trường ảnh hưởng khái toán)."""
    found = parse_message(message)
    out: dict[str, Any] = {}
    for k in _ESTIMATE_OVERRIDE_KEYS:
        v = found.get(k)
        if v is not None:
            out[k] = v.value if hasattr(v, "value") else v
    return out


def _describe_overrides(ov: dict[str, Any]) -> str:
    parts: list[str] = []
    if "floors" in ov:
        parts.append(f"xây {ov['floors']} tầng")
    if "quality_tier" in ov:
        parts.append(f"chọn mức hoàn thiện {QUALITY_TIER_VI.get(str(ov['quality_tier']), '').lower()}")
    if "work_scope" in ov:
        parts.append(f"phạm vi {WORK_SCOPE_VI.get(str(ov['work_scope']), '').lower()}")
    if ov.get("basement") is True:
        parts.append("có tầng hầm")
    elif ov.get("basement") is False:
        parts.append("không làm tầng hầm")
    if ov.get("has_mezzanine") is True:
        parts.append("có tầng lửng")
    elif ov.get("has_mezzanine") is False:
        parts.append("không làm tầng lửng")
    if "land_area_m2" in ov:
        parts.append(f"diện tích đất {format_m2(ov['land_area_m2'])}")
    return ", ".join(parts) or "thay đổi như anh/chị giả định"


def _turn_results(messages: list[Message], last_user: str | None) -> list[tuple[str, dict]]:
    """Kết quả tool trong lượt hiện tại [(tên, {"ok": ..., "result": ...} | {"is_error": ..., "error": ...})]."""
    start = 0
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.role == "user" and (last_user is None or m.content == last_user):
            start = i + 1
            break
    out: list[tuple[str, dict]] = []
    for m in messages[start:]:
        if m.role == "tool":
            try:
                data = json.loads(m.content or "{}")
            except ValueError:
                data = {}
            out.append((m.name or "", data if isinstance(data, dict) else {}))
    return out


class MockProvider:
    name = "mock"

    def __init__(self, lie_text: str | None = None):
        self.lie_text = lie_text
        # Nhật ký gọi (cho test) — giới hạn độ dài vì provider mặc định là singleton trong tiến trình server.
        self.calls: deque[dict[str, Any]] = deque(maxlen=50)

    # ------------------------------------------------------------------ API
    def chat(self, system: str, messages: list[Message], tools: list[ToolSpec],
             context: dict[str, Any] | None = None) -> LLMResponse:
        ctx = context or {}
        last_user = ctx.get("last_user_message")
        if last_user is None:
            last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        results = _turn_results(messages, last_user)
        self.calls.append({"retry": bool(ctx.get("retry")), "results": [n for n, _ in results]})
        tool_names = {t.name for t in tools}
        if not ctx.get("retry"):
            done = {n for n, _ in results}
            for i, (name, args) in enumerate(self._plan_tools(last_user, ctx)):
                if name in done or name not in tool_names:
                    continue
                if name == "get_contractor_overview" and "search_contractors" in done:
                    continue  # hệ thống vừa đề xuất nhà thầu theo tiêu chí của khách -> không lặp tổng quan
                return LLMResponse(text="", tool_calls=[ToolCall(id=f"mock_{len(results)}_{i}", name=name,
                                                                 arguments=args)], stop_reason="tool_use")
        if self.lie_text is not None:
            return LLMResponse(text=self.lie_text)
        return LLMResponse(text=self._compose(last_user, ctx, results))

    def extract(self, system: str, text: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {}

    # ------------------------------------------------------------------ chọn tool
    def _plan_tools(self, message: str, ctx: dict[str, Any]) -> list[tuple[str, dict]]:
        f = fold(message or "")
        known = ctx.get("nha_thau_da_de_xuat") or []
        slots = ctx.get("slots") or {}
        name = contractor_name_in(message or "", known)
        phone = _PHONE_RE.search(message or "")
        if "so sanh" in f:
            plan: list[tuple[str, dict]] = []
            if len(known) < 2:
                plan.append(("search_contractors", {}))
            return plan + [("compare_contractors", {})]
        if "bao gia" in f and phone:
            m = _CUSTOMER_NAME_RE.search(message)
            if m and m.group(1).strip():
                args: dict[str, Any] = {"customer_name": m.group(1).strip(), "customer_phone": phone.group(0)}
                # C-F1: chỉ gửi tới nhà thầu khách nêu tên (không nêu tên -> các nhà thầu vừa đề xuất).
                ids = [c["id"] for c in known if c.get("name") and fold(str(c["name"])) in f]
                if ids:
                    args["contractor_ids"] = ids
                return [("create_quote_request", args)]
            return []
        if _COST_Q_RE.search(f) and out_of_engine_topic(f):
            return []  # chi phí ngoài phạm vi engine: trả lời "Chưa có dữ liệu", không tra RAG
        if is_hypothetical(message) and ctx.get("khai_toan_hien_tai"):
            overrides = hypothetical_overrides(message)
            if overrides:
                return [("calculate_estimate", overrides)]  # tính thử, không đổi slot (mục 9.2)
        if _OVERVIEW_RE.search(f) and not (name and _PRICE_RE.search(f)):
            args = {}
            loc = parse_location(message)
            if loc.get("province"):
                args["province"] = loc["province"]
            bt = parse_building_type(message)
            if bt:
                args["building_type"] = str(bt)
            return [("get_contractor_overview", args)]
        if name:
            if _PROJECTS_RE.search(f) and not _PRICE_RE.search(f):
                pargs: dict[str, Any] = {"name": name}
                bt = parse_building_type(message)
                if bt:
                    pargs["building_type"] = str(bt)
                return [("get_contractor_projects", pargs)]
            return [("get_contractor_profile", {"name": name})]
        if _LIST_RE.search(f):
            sargs: dict[str, Any] = {}
            loc = parse_location(message)
            if loc.get("province"):
                sargs["province"] = loc["province"]
            return [("search_contractors", sargs)]
        if "don gia" in f:
            # Tham số khách nói trong câu hỏi ưu tiên hơn slots ("đơn giá hoàn thiện ở HCM" khi slot là xây thô).
            rate_args: dict[str, Any] = {}
            for key, val in (("building_type", parse_building_type(message)), ("work_scope", parse_work_scope(message)),
                             ("quality_tier", parse_quality_tier(message)),
                             ("province", parse_location(message).get("province"))):
                if val:
                    rate_args[key] = str(val)
            if all(rate_args.get(k) or slots.get(k) for k in ("building_type", "province", "work_scope")):
                return [("get_reference_rates", rate_args)]
        if ctx.get("intent") == "question" and ctx.get("stage") != "collect" and not _COST_Q_RE.search(f):
            return [("search_knowledge", {"query": message[:300]})]
        return []

    # ------------------------------------------------------------------ viết văn bản
    def _compose(self, message: str, ctx: dict[str, Any], results: list[tuple[str, dict]]) -> str:
        f = fold(message or "")
        parts: list[str] = []
        searched_knowledge = False
        for name, data in results:
            if data.get("is_error"):
                err = str(data.get("error") or "")
                parts.append("Dạ, em chưa lấy được thông tin cho yêu cầu này"
                             + (f": {err}" if err and not re.search(r"\d", err) else "") + ".")
                continue
            searched_knowledge = searched_knowledge or name == "search_knowledge"
            text = self._tool_text(name, data.get("result"), f, message, ctx)
            if text:
                parts.append(text)
        if parts:
            chosen = ctx.get("phuong_an_da_chon")
            if chosen:  # khách vừa chọn một phương án ngân sách (W-F1)
                parts = [f"Dạ, em đã áp dụng phương án “{chosen}” và tính lại theo thông tin mới.", *parts]
            # Chỉ đoạn đầu mở bằng "Dạ," (không lặp "Dạ" ở mỗi đoạn).
            parts = parts[:1] + [_uc_first(p.removeprefix("Dạ, ")) for p in parts[1:]]
            return "\n\n".join(parts)
        if ctx.get("intent") == "out_of_scope":
            return ("Dạ, em xin phép chỉ hỗ trợ các nội dung về xây dựng: khái toán chi phí, tìm và so sánh nhà thầu "
                    "trên Dezon ạ.")
        topic = out_of_engine_topic(f) if _COST_Q_RE.search(f) else None
        if topic:  # C-F3: chi phí ngoài phạm vi engine
            label, reason = topic
            scope = "khái toán của anh/chị" if ctx.get("khai_toan_hien_tai") else "khái toán sơ bộ của Dezon"
            return (f"Dạ, hiện Dezon chưa có đơn giá cho hạng mục {label}: {NO_DATA}. Khoản này chưa gồm trong {scope}; "
                    f"chi phí phụ thuộc {reason}. Anh/chị nên gửi yêu cầu báo giá để nhà thầu khảo sát và báo giá cụ "
                    "thể ạ.")
        if "bao gia" in f:
            if not ctx.get("nha_thau_da_de_xuat"):
                # Chưa có nhà thầu đề xuất -> hệ thống không hiện form (form cần ít nhất 1 nhà thầu).
                return ("Dạ, để gửi yêu cầu báo giá, em cần tìm nhà thầu phù hợp với công trình của anh/chị trước. "
                        "Anh/chị cho em thêm vài thông tin về công trình nhé.")
            return ("Dạ, anh/chị vui lòng điền họ tên và số điện thoại vào form yêu cầu báo giá bên dưới, em sẽ "
                    "chuyển tới nhà thầu để khảo sát và gửi báo giá chi tiết ạ.")
        if ctx.get("he_thong_se_tu_them") and ctx.get("intent") != "question":
            if _GREETING_RE.match(f.strip()):
                return "Dạ, em chào anh/chị ạ."
            return ""  # C-F8: câu hỏi/tóm tắt do hệ thống thêm là đủ — không thêm câu "đã ghi nhận" khuôn mẫu
        if ctx.get("intent") == "question" and ctx.get("he_thong_se_tu_them"):
            return "Dạ, để tư vấn chính xác em cần thêm một vài thông tin về công trình ạ."
        if ctx.get("thong_tin_moi"):
            return "Dạ, em đã ghi nhận thông tin của anh/chị."
        if searched_knowledge or (ctx.get("intent") == "question" and _COST_Q_RE.search(f)):
            return (f"Dạ, em chưa tìm thấy thông tin liên quan trên Dezon cho câu hỏi này ({NO_DATA}). Anh/chị có thể "
                    "hỏi em về khái toán chi phí, nhà thầu phù hợp hoặc gửi yêu cầu báo giá ạ.")
        return ("Dạ, em có thể hỗ trợ anh/chị khái toán chi phí, tìm và so sánh nhà thầu, hoặc gửi yêu cầu báo giá "
                "trên Dezon. Anh/chị cần em hỗ trợ gì thêm ạ?")

    def _tool_text(self, name: str, r: Any, f: str, message: str = "", ctx: dict[str, Any] | None = None) -> str:
        ctx = ctx or {}
        if not isinstance(r, dict):
            return build_fallback_text([(name, r)]) if r else ""
        if name == "calculate_estimate":
            forced = "calculate_estimate" in (ctx.get("cong_cu_he_thong_da_chay") or [])
            if not forced and is_hypothetical(message) and ctx.get("khai_toan_hien_tai"):
                return self._hypothetical(r, message, ctx)
            return self._estimate(r)
        if name == "suggest_budget_adjustments":
            return self._budget(r)
        if name == "search_contractors":
            return self._match(r, f, ctx)
        if name == "compare_contractors":
            names = [str(c.get("name", "")) for c in r.get("contractors") or []]
            return (f"Dạ, em đã lập bảng so sánh các nhà thầu: {', '.join(names)}. Ô \"{NO_DATA}\" nghĩa là Dezon "
                    "chưa có thông tin cho tiêu chí đó; anh/chị nên mở hồ sơ gốc để đối chiếu thêm. "
                    "Mức phù hợp được đánh giá theo tiêu chí của anh/chị, không phải xếp hạng chất lượng thi công.")
        if name == "get_contractor_profile":
            return self._profile(r, f)
        if name == "get_contractor_overview":
            # Template tổng quan (mục 8.4): số nhà thầu, ai có công trình đúng loại; chi tiết ở bảng — không xếp hạng.
            text = build_fallback_text([(name, r)])
            if _CHEAP_RE.search(f) and r.get("contractors"):
                text += (" Về chi phí: Dezon chỉ có đơn giá tham khảo trong hồ sơ của từng nhà thầu (các dòng đơn giá "
                         "trong bảng); chi phí thực tế phụ thuộc thiết kế và vật tư, anh/chị nên gửi yêu cầu báo giá để "
                         "so sánh ạ.")
            return text
        if name == "update_project_requirements":
            return "Dạ, em đã cập nhật thông tin công trình."
        return build_fallback_text([(name, r)])

    @staticmethod
    def _hypothetical(r: dict, message: str, ctx: dict[str, Any]) -> str:
        """C-F3: phương án giả định — tính thử bằng engine, không đổi thông tin công trình."""
        cost = _ev(r.get("cost"))
        desc = _describe_overrides(hypothetical_overrides(message))
        if _disp(cost) == NO_DATA:
            return (f"Dạ, với phương án giả định ({desc}) em chưa có đơn giá tham chiếu phù hợp nên chưa tính được: "
                    f"{NO_DATA}.")
        cur = str((ctx.get("khai_toan_hien_tai") or {}).get("cost") or "")
        text = f"Dạ, nếu {desc}, chi phí khái toán sơ bộ khoảng {_disp(cost)}"
        if cur and cur != NO_DATA:
            text += f" (phương án hiện tại: {cur})"
        return (text + ". Đây là phương án tính thử để anh/chị tham khảo (chi tiết ở bảng bên dưới), em chưa thay đổi "
                "thông tin công trình; nếu muốn áp dụng, anh/chị nhắn \"đổi sang ...\" giúp em nhé. " + ESTIMATE_NOTE)

    @staticmethod
    def _estimate(r: dict) -> str:
        """Lời dẫn ngắn + giải thích gồm/chưa gồm/yếu tố tăng (mục 6.5); bảng khái toán do block hiển thị (W-F7)."""
        cost, rate = _ev(r.get("cost")), _ev(r.get("rate"))
        if _disp(cost) == NO_DATA:
            return (f"Dạ, hiện em chưa thể khái toán vì chưa có đơn giá tham chiếu phù hợp: {NO_DATA}. "
                    "Anh/chị có thể gửi yêu cầu báo giá để nhà thầu khảo sát trực tiếp ạ.")
        scope = WORK_SCOPE_VI.get(str(_ev(r.get("input")).get("work_scope")), "").lower()
        assumptions = [str(a) for a in r.get("assumptions") or []]
        lead = (f"Dạ, em đã tính khái toán sơ bộ{f' phạm vi {scope}' if scope else ''}: chi phí dự kiến khoảng "
                f"{_disp(cost)}, theo đơn giá tham chiếu {_disp(rate)}.")
        national = next((a for a in assumptions if a.startswith(NATIONAL_RATE_PREFIX)), None)
        if national:  # C-F11: nói rõ trong văn bản khi dùng đơn giá mặc định toàn quốc
            lead += f" Lưu ý: em đang {_lc_first(national)}."
        if rate.get("is_stale"):
            lead += f" Đơn giá tham chiếu{_src(rate)} có thể đã cũ."
        lines = [lead]
        explain: list[str] = []
        includes = next((a for a in assumptions if a.startswith("Phạm vi ") and ": gồm" in a), None)
        if includes:
            explain.append("Khái toán " + includes.split(": ", 1)[1].rstrip(".") + ".")
        excl = [str(x).split(" (")[0] for x in (r.get("exclusions") or [])[:3]]
        if excl:
            lead_ex = "Ngoài ra, khái toán chưa gồm " if includes and "chưa gồm" in includes else "Khái toán chưa gồm "
            explain.append(lead_ex + ", ".join(_lc_first(x) for x in excl) + ".")
        if explain:
            lines.append(" ".join(explain))
        drivers = [str(x).replace(": ", " — ", 1) for x in (r.get("cost_drivers") or [])[:2]]
        if drivers:
            lines.append("Chi phí có thể tăng nếu: " + "; ".join(_lc_first(d) for d in drivers) + ".")
        lines.append("Chi tiết hệ số, giả định và nguồn đơn giá ở bảng bên dưới. " + ESTIMATE_NOTE)
        return "\n".join(lines)

    @staticmethod
    def _budget(r: dict) -> str:
        """Lời dẫn ngắn; danh sách phương án do block budget_options hiển thị (không chép lại)."""
        target = _disp(r.get("target_budget"))
        options = [o for o in (r.get("options") or []) if isinstance(o, dict)]
        head = f"Dạ, với ngân sách {target}, khái toán hiện tại là {_disp(r.get('current_cost'))}"
        if not options:
            return (head + ". Hiện em chưa có phương án điều chỉnh phù hợp; anh/chị có thể gửi yêu cầu báo giá để nhà "
                    "thầu tư vấn thêm ạ.")
        lines = [head + f", em đã tính {len(options)} phương án điều chỉnh trong bảng bên dưới."]
        fit = [o for o in options if o.get("fits_budget")]
        if fit:
            o = fit[0]
            more = f" (cùng {len(fit) - 1} phương án khác)" if len(fit) > 1 else ""
            lines.append(f"Phương án “{o.get('label_vi', '')}” ({_disp(o.get('cost'))}){more} nằm trong ngân sách của "
                         "anh/chị.")
        else:
            cheapest = min(options, key=lambda o: o.get("cost_min") or 0)
            lines.append(f"Phương án tiết kiệm nhất là “{cheapest.get('label_vi', '')}” "
                         f"({_disp(cheapest.get('cost'))}); theo đơn giá hiện có, chưa có phương án nào đưa chi phí về "
                         f"trong ngân sách {target} của anh/chị.")
        lines.append("Anh/chị bấm \"Chọn phương án này\" để em tính lại theo phương án đó nhé.")
        return " ".join(lines)

    @staticmethod
    def _match(r: dict, f: str = "", ctx: dict[str, Any] | None = None) -> str:
        """Lời dẫn ngắn; lý do/điểm lưu ý từng nhà thầu do block contractor_list hiển thị (không chép lại)."""
        ctx = ctx or {}
        results = r.get("results") or []
        if not results:
            prov = _ev(r.get("criteria")).get("province") or "khu vực của anh/chị"
            return (f"Dạ, hiện em chưa tìm thấy nhà thầu phù hợp tại {prov} trên Dezon. Anh/chị có thể tìm nhà thầu ở "
                    "khu vực khác hoặc sửa thông tin công trình bằng các nút bên dưới ạ.")
        names = ", ".join(str(_ev(m.get("contractor")).get("name", "")) for m in results)
        ids = {_ev(m.get("contractor")).get("id") for m in results}
        known = set(ctx.get("nha_thau_luot_truoc") or [])  # nhà thầu đã đề xuất trước lượt này
        same = bool(known) and ids <= known
        if same and re.search(r"\bkhac\b", f):  # C-F7: "tìm nhà thầu khác" nhưng không có nhà thầu mới
            total = r.get("total_candidates") or len(results)
            if total <= len(results):
                return (f"Dạ, hiện Dezon chỉ có {total} nhà thầu phù hợp với tiêu chí của anh/chị ({names}), em đã giới "
                        "thiệu ở trên ạ. Anh/chị có thể tìm ở khu vực khác, điều chỉnh tiêu chí, hoặc so sánh và gửi "
                        "yêu cầu báo giá cho các nhà thầu này.")
            return (f"Dạ, theo tiêu chí hiện tại, các nhà thầu phù hợp nhất vẫn là {names}. Anh/chị có thể tìm ở khu "
                    "vực khác hoặc điều chỉnh tiêu chí để em tìm thêm ạ.")
        if same and "search_contractors" in (ctx.get("cong_cu_he_thong_da_chay") or []):
            return f"Dạ, em đã cập nhật mức phù hợp (gồm mức phù hợp ngân sách) của {names} trong danh sách bên dưới."
        text = f"Dạ, em đề xuất {len(results)} nhà thầu phù hợp nhất với tiêu chí của anh/chị trên Dezon: {names}."
        if r.get("relaxed_to_neighbors"):
            text += " Một số nhà thầu ở tỉnh lân cận do khu vực của anh/chị còn ít lựa chọn."
        return (text + " Lý do và điểm cần lưu ý của từng nhà thầu có trong danh sách bên dưới; thứ tự là mức phù hợp "
                "với tiêu chí của anh/chị, không phải xếp hạng chất lượng thi công.")

    @staticmethod
    def _profile(r: dict, f: str) -> str:
        """Trả lời ĐÚNG câu hỏi (đơn giá, kinh nghiệm, bảo hành...); toàn bộ hồ sơ do thẻ contractor_detail hiển thị."""
        card = _ev(r.get("card"))
        info = {**_ev(card.get("evidence")), **_ev(r.get("info"))}
        name = card.get("name", "nhà thầu")
        if _PRICE_RE.search(f):
            rough, finish = info.get("rough_price"), info.get("finish_price")
            text = (f"Dạ, theo hồ sơ {name} trên Dezon: đơn giá xây thô: {_disp(rough)}{_src(rough)}; "
                    f"đơn giá hoàn thiện cơ bản: {_disp(finish)}{_src(finish)}.")
            if _disp(rough) == NO_DATA and _disp(finish) == NO_DATA:
                text += (" Anh/chị có thể gửi yêu cầu báo giá để nhà thầu cung cấp đơn giá cụ thể cho công trình "
                         "của mình ạ.")
            return text
        for rx, keys, topic in _PROFILE_TOPICS:
            if not rx.search(f):
                continue
            have = [(k, info[k]) for k in keys if k in info and _disp(info[k]) != NO_DATA]
            if have:
                facts = "; ".join(f"{fallback.INFO_LABEL_VI.get(k, k).lower()}: {_disp(ev)}{_src(ev)}"
                                  for k, ev in have)
                return f"Dạ, theo hồ sơ {name} trên Dezon, {facts}. Các thông tin khác có trong thẻ hồ sơ bên dưới."
            if topic == "bảo hành":
                return (f"Dạ, Dezon chưa có thông tin bảo hành của {name} ({NO_DATA}) nên em không xác nhận được ạ. "
                        "Anh/chị nên hỏi rõ điều khoản bảo hành khi nhận báo giá từ nhà thầu.")
            return (f"Dạ, hồ sơ {name} trên Dezon hiện chưa có thông tin {topic} nên em chưa trả lời được ạ. Các "
                    "thông tin khác có trong thẻ hồ sơ bên dưới.")
        badge = f" ({card['badge']})" if card.get("badge") else ""
        return (f"Dạ, em gửi anh/chị hồ sơ {name}{badge} trên Dezon ở thẻ bên dưới: khu vực, đơn giá tham khảo, công "
                "trình có trang dự án, bảo hành và link hồ sơ gốc. Anh/chị có thể so sánh với nhà thầu khác hoặc gửi "
                "yêu cầu báo giá ạ.")
