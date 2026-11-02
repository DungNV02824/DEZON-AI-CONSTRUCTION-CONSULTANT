"""MockProvider — "LLM" kịch bản, xác định, KHÔNG gọi mạng (demo toàn bộ luồng MVP khi chưa có API key).

Quyết định gọi tool dựa trên tin nhắn cuối + khối trạng thái `context` do orchestrator truyền (slots, stage, intent,
nhà thầu đã đề xuất). Văn bản viết bằng template CHỈ dùng chuỗi `display` trong kết quả tool:
- không bao giờ dùng "tốt nhất"; số dự án luôn là "công trình ghi nhận trên Dezon";
- khái toán luôn nhắc "không phải báo giá"; trường thiếu nói "Chưa có dữ liệu".

`MockProvider(lie_text=...)`: chế độ "nói dối" cho test guardrail — luôn trả văn bản cố định (vd có số bịa).
`extract()` luôn trả {} (slot filling dùng bộ trích quy tắc).
"""
from __future__ import annotations

import json
import re
from collections import deque
from typing import Any

from app.ai.fallback import build_fallback_text
from app.ai.normalize import fold, parse_building_type, parse_location, parse_quality_tier, parse_work_scope
from app.ai.providers.base import LLMResponse, Message, ToolCall, ToolSpec
from app.formatting import NO_DATA
from app.schemas.common import WORK_SCOPE_VI

ESTIMATE_NOTE = "Đây là khái toán sơ bộ, không phải báo giá thi công."
PROJECT_NOTE = ("Số công trình là số công trình ghi nhận trên Dezon, không đồng nghĩa với số công trình đã hoàn "
                "thành.")

_OVERVIEW_RE = re.compile(r"tot nhat|uy tin|nen chon|dang tin|tot tai|tot o |thi cong .{0,40}\btot\b|nha thau nao")
_LIST_RE = re.compile(r"(tim|de xuat|goi y|gioi thieu|liet ke)\s+(cho\s+\w+\s+)?(cac\s+|mot so\s+|vai\s+)?nha thau")
_PRICE_RE = re.compile(r"\bgia\b|don gia|bao nhieu tien|chi phi")
_PROJECTS_RE = re.compile(r"\bdu an\b|\bcong trinh\b")
_PHONE_RE = re.compile(r"(?:\+?84|0)[\d\s.\-]{8,13}\d")
_CUSTOMER_NAME_RE = re.compile(
    r"(?:tên(?:\s+(?:tôi|em|mình|tui))?\s+là|tôi\s+là|mình\s+là|em\s+là)\s+([^\d,.;:!?]+?)"
    r"(?=\s*(?:[,.;:!?]|\bsố\b|\bsđt\b|\bsdt\b|\bđiện\b|$))", re.IGNORECASE)
_NAME_TRIGGERS = ("của", "nhà thầu", "hồ sơ", "đơn vị", "công ty", "về")
_STOP_NAME_WORDS = {"TP.", "TP", "Tp", "Tp.", "Quận", "Huyện", "Thành", "Tỉnh"}


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
    return None


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
                return [("create_quote_request", {"customer_name": m.group(1).strip(),
                                                  "customer_phone": phone.group(0)})]
            return []
        if _OVERVIEW_RE.search(f) and not (name and _PRICE_RE.search(f)):
            args: dict[str, Any] = {}
            loc = parse_location(message)
            if loc.get("province"):
                args["province"] = loc["province"]
            bt = parse_building_type(message)
            if bt:
                args["building_type"] = str(bt)
            return [("get_contractor_overview", args)]
        if name:
            if _PROJECTS_RE.search(f) and not _PRICE_RE.search(f):
                return [("get_contractor_projects", {"name": name})]
            return [("get_contractor_profile", {"name": name})]
        if _LIST_RE.search(f):
            return [("search_contractors", {})]
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
        if ctx.get("intent") == "question" and ctx.get("stage") != "collect":
            return [("search_knowledge", {"query": message[:300]})]
        return []

    # ------------------------------------------------------------------ viết văn bản
    def _compose(self, message: str, ctx: dict[str, Any], results: list[tuple[str, dict]]) -> str:
        f = fold(message or "")
        parts: list[str] = []
        for name, data in results:
            if data.get("is_error"):
                err = str(data.get("error") or "")
                parts.append("Dạ, em chưa lấy được thông tin cho yêu cầu này"
                             + (f": {err}" if err and not re.search(r"\d", err) else "") + ".")
                continue
            res = data.get("result")
            text = self._tool_text(name, res, f)
            if text:
                parts.append(text)
        if parts:
            return "\n\n".join(parts)
        if ctx.get("intent") == "out_of_scope":
            return ("Dạ, em xin phép chỉ hỗ trợ các nội dung về xây dựng: khái toán chi phí, tìm và so sánh nhà thầu "
                    "trên Dezon ạ.")
        if "bao gia" in f:
            if not ctx.get("nha_thau_da_de_xuat"):
                # Chưa có nhà thầu đề xuất -> hệ thống không hiện form (form cần ít nhất 1 nhà thầu).
                return ("Dạ, để gửi yêu cầu báo giá, em cần tìm nhà thầu phù hợp với công trình của anh/chị trước. "
                        "Anh/chị cho em thêm vài thông tin về công trình nhé.")
            return ("Dạ, anh/chị vui lòng điền họ tên và số điện thoại vào form yêu cầu báo giá bên dưới, em sẽ "
                    "chuyển tới nhà thầu để khảo sát và gửi báo giá chi tiết ạ.")
        if ctx.get("thong_tin_moi"):
            return "Dạ, em đã ghi nhận thông tin của anh/chị."
        if ctx.get("intent") == "question" and ctx.get("he_thong_se_tu_them"):
            return "Dạ, để tư vấn chính xác em cần thêm một vài thông tin về công trình ạ."
        if ctx.get("he_thong_se_tu_them"):
            return ""
        return ("Dạ, em có thể hỗ trợ anh/chị khái toán chi phí, tìm và so sánh nhà thầu, hoặc gửi yêu cầu báo giá "
                "trên Dezon. Anh/chị cần em hỗ trợ gì thêm ạ?")

    def _tool_text(self, name: str, r: Any, f: str) -> str:
        if not isinstance(r, dict):
            return build_fallback_text([(name, r)]) if r else ""
        if name == "calculate_estimate":
            return self._estimate(r)
        if name == "suggest_budget_adjustments":
            return self._budget(r)
        if name == "search_contractors":
            return self._match(r)
        if name == "compare_contractors":
            names = [str(c.get("name", "")) for c in r.get("contractors") or []]
            return (f"Dạ, em đã lập bảng so sánh các nhà thầu: {', '.join(names)}. Ô \"{NO_DATA}\" nghĩa là Dezon "
                    "chưa có thông tin cho tiêu chí đó; anh/chị nên mở hồ sơ gốc để đối chiếu thêm. "
                    "Mức phù hợp được đánh giá theo tiêu chí của anh/chị, không phải xếp hạng chất lượng thi công.")
        if name == "get_contractor_profile":
            return self._profile(r, f)
        if name == "get_contractor_overview":
            # Template tổng quan (mục 8.4): số nhà thầu phù hợp, công trình thực tế, đơn giá, bảo hành, xác minh,
            # kèm câu "Dezon chưa có dữ liệu đánh giá chất lượng thi công sau bàn giao" — không xếp hạng.
            return build_fallback_text([(name, r)])
        if name == "update_project_requirements":
            return "Dạ, em đã cập nhật thông tin công trình."
        return build_fallback_text([(name, r)])

    @staticmethod
    def _estimate(r: dict) -> str:
        cost, rate = _ev(r.get("cost")), _ev(r.get("rate"))
        if _disp(cost) == NO_DATA:
            return (f"Dạ, hiện em chưa thể khái toán vì chưa có đơn giá tham chiếu phù hợp: {NO_DATA}. "
                    "Anh/chị có thể gửi yêu cầu báo giá để nhà thầu khảo sát trực tiếp ạ.")
        scope = WORK_SCOPE_VI.get(str(_ev(r.get("input")).get("work_scope")), "").lower()
        lines = [f"Dạ, em đã tính khái toán sơ bộ: chi phí dự kiến khoảng {_disp(cost)}, theo đơn giá tham chiếu "
                 f"{_disp(rate)}{_src(rate)}."]
        if scope:
            lines.append(f"Chi phí tính cho phạm vi {scope}; chi tiết hệ số và giả định có trong bảng khái toán.")
        if r.get("exclusions"):
            lines.append("Chưa bao gồm: " + "; ".join(str(x) for x in r["exclusions"][:4]) + ".")
        if r.get("cost_drivers"):
            lines.append("Chi phí có thể tăng nếu: " + "; ".join(str(x) for x in r["cost_drivers"][:3]) + ".")
        for w in (r.get("warnings") or [])[:2]:
            lines.append(f"Lưu ý: {w}.")
        lines.append(ESTIMATE_NOTE)
        return "\n".join(lines)

    @staticmethod
    def _budget(r: dict) -> str:
        lines = [f"Với ngân sách {_disp(r.get('target_budget'))}, khái toán hiện tại là {_disp(r.get('current_cost'))}."
                 " Một số phương án điều chỉnh do hệ thống tính:"]
        options = r.get("options") or []
        if not options:
            lines.append("- Hiện chưa có phương án điều chỉnh phù hợp.")
        for o in options[:5]:
            fit = " — nằm trong ngân sách" if o.get("fits_budget") else ""
            lines.append(f"- {o.get('label_vi', '')}: {_disp(o.get('cost'))}{fit}")
        return "\n".join(lines)

    @staticmethod
    def _match(r: dict) -> str:
        results = r.get("results") or []
        if not results:
            return ("Dạ, hiện em chưa tìm thấy nhà thầu phù hợp với tiêu chí của anh/chị trên Dezon. Anh/chị có thể "
                    "nới rộng khu vực hoặc điều chỉnh tiêu chí ạ.")
        lines = [f"Dạ, em đề xuất {len(results)} nhà thầu phù hợp nhất với tiêu chí của anh/chị trên Dezon:"]
        for m in results:
            c = _ev(m.get("contractor"))
            reasons = [str(x.get("text")) for x in (m.get("reasons") or []) if x.get("text")][:3]
            lines.append(f"- {c.get('name', '')} ({m.get('fit_label', '')})" + (f": {'; '.join(reasons)}" if reasons
                                                                                   else "") + ".")
        lines.append(PROJECT_NOTE)
        lines.append("Thứ tự trên là mức phù hợp với tiêu chí của anh/chị, không phải xếp hạng chất lượng thi công. "
                     "Anh/chị có thể xem hồ sơ, so sánh hoặc gửi yêu cầu báo giá ạ.")
        return "\n".join(lines)

    @staticmethod
    def _profile(r: dict, f: str) -> str:
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
        return build_fallback_text([("get_contractor_profile", r)])
