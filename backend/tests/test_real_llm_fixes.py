"""Lỗi phát hiện khi chạy với LLM thật (gpt-4.1-mini):

1. LLM chép lại câu hỏi do code dựng; bỏ câu hỏi nhưng còn sót ghi chú trong ngoặc "(phần sàn xây, không tính sân)".
2. Lượt tóm tắt: LLM tự viết "Em tóm tắt lại: ..." rồi code nối thêm bản tóm tắt -> hai lần.
3. Có tầng lửng nhưng không hỏi diện tích lửng (engine tự giả định lửng = cả sàn) -> hỏi thay vì giả định.
"""
import pytest

from app.ai import orchestrator, slot_filling, tools
from app.ai.dialog import (
    BUILDING_AREA_TEXT,
    MEZZANINE_AREA_TEXT,
    MORE_VALUE,
    ROOF_TEXT,
    build_summary,
    edit_field_question,
    mezzanine_area_replies,
    next_question,
)
from app.ai.normalize import parse_message
from app.ai.providers.base import LLMResponse
from app.ai.providers.mock import MockProvider
from app.ai.state import SessionState
from app.ai.tools import ToolContext, execute_tool
from app.schemas.common import BuildingType, WorkScope
from app.schemas.slots import Slots, missing_required, to_estimate_input
from app.services import chat_service
from tests.wizard import question, step

drop = orchestrator._drop_questions


class ScriptedProvider:
    """LLM giả: luôn trả `text` (mô phỏng văn bản LLM thật); extract trả `extracted`."""
    name = "scripted"

    def __init__(self, text: str, extracted: dict | None = None):
        self.text, self.extracted = text, extracted or {}

    def chat(self, system, messages, tools_, context=None):
        return LLMResponse(text=self.text)

    def extract(self, system, text, schema):
        return dict(self.extracted)


def _core(**kw) -> Slots:
    base = dict(building_type=BuildingType.nha_pho, province="TP. Hồ Chí Minh", district="TP. Thủ Đức",
                land_width_m=5, land_length_m=20, land_area_m2=100, floors=4, work_scope=WorkScope.tron_goi)
    return Slots(**{**base, **kw})


def _new(db):
    return chat_service.create_session(db).session_id


def _say(db, sid, msg, provider=None):
    return orchestrator.handle_message(db, sid, msg, provider=provider or MockProvider())


# =============================================================================================
# 1. Ghi chú trong ngoặc của câu hỏi bị sót lại
# =============================================================================================
@pytest.mark.parametrize("llm", [
    # Nguyên văn lỗi thật: câu hỏi + ghi chú trong ngoặc sau dấu "?".
    "Cảm ơn anh/chị đã cung cấp thông tin. Diện tích xây dựng mỗi tầng khoảng bao nhiêu m² ạ? "
    "(phần sàn xây, không tính sân)",
    # Ghi chú xuống dòng riêng.
    "Cảm ơn anh/chị đã cung cấp thông tin.\nDiện tích xây dựng mỗi tầng là bao nhiêu ạ?\n(phần sàn xây, không tính sân)",
    # Ghi chú dính vào câu ghi nhận.
    "Cảm ơn anh/chị đã cung cấp thông tin. (phần sàn xây, không tính sân)",
    "Cảm ơn anh/chị đã cung cấp thông tin (phần sàn xây, không tính sân).",
    # Chép nguyên câu hỏi không có dấu "?" ở cuối câu.
    "Cảm ơn anh/chị đã cung cấp thông tin. Diện tích xây dựng mỗi tầng khoảng bao nhiêu m² ạ (phần sàn xây, "
    "không tính sân).",
])
def test_question_echo_and_parenthetical_dropped(llm):
    assert drop(llm, BUILDING_AREA_TEXT) == "Cảm ơn anh/chị đã cung cấp thông tin."


def test_orphan_example_parenthetical_dropped_with_question():
    llm = "Dạ, em ghi nhận không có hầm. Mái nhà dự kiến làm loại nào ạ?\n(ví dụ: mái tôn, mái bê tông, mái ngói)"
    assert drop(llm, ROOF_TEXT) == "Dạ, em ghi nhận không có hầm."
    assert drop("Mái nhà làm loại nào ạ? (ví dụ: mái tôn, mái ngói)", ROOF_TEXT) == ""
    # Gợi ý ví dụ viết thành câu riêng (lỗi thật ở lượt hỏi mái).
    assert drop("Ví dụ mái tôn, mái bê tông, mái ngói trên khung kèo sắt hoặc bê tông.", ROOF_TEXT) == ""
    assert drop("Dạ, em ghi nhận. Chẳng hạn: mái tôn, mái ngói.", ROOF_TEXT) == "Dạ, em ghi nhận."
    # Khách hỏi (questions=False): giữ ví dụ trong câu trả lời.
    assert drop("Ví dụ mái tôn rẻ hơn mái ngói.", ROOF_TEXT, questions=False) == "Ví dụ mái tôn rẻ hơn mái ngói."


def test_unrelated_parenthetical_and_statements_kept():
    assert drop("Đơn giá 4,3–4,7 triệu/m² (nguồn: Dezon).", BUILDING_AREA_TEXT) == \
        "Đơn giá 4,3–4,7 triệu/m² (nguồn: Dezon)."
    assert drop("Dạ, em ghi nhận nhà phố tại TP. Thủ Đức.", BUILDING_AREA_TEXT) == \
        "Dạ, em ghi nhận nhà phố tại TP. Thủ Đức."
    # Khách hỏi (questions=False): giữ câu trả lời, chỉ bỏ phần chép lại câu hỏi của code.
    out = drop("Móng cọc thường dùng cho nhà từ 4 tầng. Diện tích xây dựng mỗi tầng khoảng bao nhiêu m² ạ? "
               "(phần sàn xây, không tính sân)", BUILDING_AREA_TEXT, questions=False)
    assert out == "Móng cọc thường dùng cho nhà từ 4 tầng."


@pytest.mark.db
def test_collect_turn_has_question_and_note_once(db):
    sid = _new(db)
    llm = ("Cảm ơn anh/chị đã cung cấp thông tin. Diện tích xây dựng mỗi tầng khoảng bao nhiêu m² ạ? "
           "(phần sàn xây, không tính sân)")
    _say(db, sid, "Tôi muốn xây nhà phố ở Thủ Đức, đất 5x20, 1 trệt 3 lầu, trọn gói")
    r = _say(db, sid, MORE_VALUE, ScriptedProvider(llm))  # "Cung cấp thêm thông tin" -> câu chi tiết đầu tiên
    # Câu hỏi + ghi chú nằm trên thẻ question_step (một lần); văn bản chỉ còn lời ghi nhận của LLM.
    assert r.text == "Cảm ơn anh/chị đã cung cấp thông tin."
    assert question(r) == "Diện tích xây dựng mỗi tầng (m²)?" and step(r).hint == "Phần sàn xây, không tính sân."


# =============================================================================================
# 2. Tóm tắt hai lần
# =============================================================================================
def _summary_tail() -> str:
    s = _core(building_area_m2=80, basement=False, has_mezzanine=False, roof_type="roof_concrete")
    return build_summary(s)[0]


def test_llm_summary_echo_dropped():
    tail = _summary_tail()
    echo = tail.split(" Anh/chị xác nhận")[0]  # LLM chép nguyên câu tóm tắt
    assert echo.startswith("Em tóm tắt lại:")
    assert drop(echo, tail, summary=True) == ""
    assert drop(f"Dạ, cảm ơn anh/chị. {echo}", tail, summary=True) == "Dạ, cảm ơn anh/chị."


def test_llm_summary_paraphrase_and_list_dropped():
    tail = _summary_tail()
    llm = ("Dạ, cảm ơn anh/chị. Em xin tóm tắt thông tin như sau:\n- Loại công trình: Nhà phố\n"
           "- Khu vực: TP. Thủ Đức\nDiện tích đất: 100 m²\nSố tầng: 1 trệt 3 lầu\n\n"
           "Anh/chị kiểm tra và xác nhận giúp em nhé.")
    assert drop(llm, tail, summary=True) == "Dạ, cảm ơn anh/chị."
    # Câu không phải tóm tắt vẫn giữ.
    assert drop("Dạ, mái bằng là lựa chọn phổ biến cho nhà phố ạ.", tail, summary=True) == \
        "Dạ, mái bằng là lựa chọn phổ biến cho nhà phố ạ."


@pytest.mark.db
def test_budget_question_after_estimate_not_duplicated(db):
    """Lượt khái toán: LLM chép lại câu hỏi ngân sách mà code nối vào cuối -> chỉ còn một lần."""
    sid = _new(db)
    _say(db, sid, "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu trọn gói, xây 80m2, không hầm, không lửng, mái bằng")
    r = _say(db, sid, "Đúng rồi, tính khái toán giúp tôi",
             ScriptedProvider(f"Dạ, em đã tính khái toán cho anh/chị. {orchestrator.BUDGET_TAIL}"))
    assert any(name == "calculate_estimate" for name, _ in r.tool_results)
    assert r.text.count(orchestrator.BUDGET_TAIL) == 1 and r.text.endswith(orchestrator.BUDGET_TAIL)
    assert r.text.startswith("Dạ, em đã tính khái toán cho anh/chị.")


@pytest.mark.db
def test_summary_turn_has_single_summary(db):
    sid = _new(db)
    msg = "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu trọn gói, xây 80m2, không hầm, không lửng, mái bằng"
    tail = build_summary(_core(building_area_m2=80, basement=False, has_mezzanine=False, roof_type="roof_concrete"))[0]
    echo = tail.split(" Anh/chị xác nhận")[0]
    r = _say(db, sid, msg, ScriptedProvider(echo))
    assert "summary_confirm" in [b.type for b in r.ui_blocks]
    assert r.text.count("Em tóm tắt lại") == 1 and r.text.count("xác nhận") == 1
    assert r.text.endswith("Anh/chị xác nhận để em tính khái toán nhé?")


# =============================================================================================
# 3. Hỏi diện tích tầng lửng thay vì giả định
# =============================================================================================
def test_mezzanine_area_asked_only_when_has_mezzanine():
    s = _core(building_area_m2=80, basement=False, has_mezzanine=True, roof_type="roof_concrete")
    assert next_question(s) is None  # chỉ hỏi khi khách bấm "Cung cấp thêm thông tin"
    q = next_question(s, more=True)
    assert q.slots == ["mezzanine_area_m2"] and q.text == MEZZANINE_AREA_TEXT == "Tầng lửng rộng khoảng bao nhiêu m² ạ?"
    assert [o.label for o in q.quick_replies.options] == ["Khoảng 1/2 sàn (40 m²)", "Bằng cả sàn (80 m²)", "Chưa biết"]
    for flag in (False, None):
        assert "mezzanine_area_m2" not in missing_required(s.model_copy(update={"has_mezzanine": flag}))
    # Chưa có diện tích xây dựng -> mốc là diện tích đất (như engine).
    labels = [o.label for o in mezzanine_area_replies(_core(has_mezzanine=True))]
    assert labels[:2] == ["Khoảng 1/2 sàn (50 m²)", "Bằng cả sàn (100 m²)"]


@pytest.mark.parametrize("value,expected", [("Tầng lửng 40 m2", 40), ("Tầng lửng 80 m2", 80)])
def test_mezzanine_quick_replies_parse(value, expected):
    s = _core(building_area_m2=80, basement=False, has_mezzanine=True)
    assert value in [o.value for o in mezzanine_area_replies(s)]
    u = slot_filling.extract(value, s, expected_slots=["mezzanine_area_m2"])
    assert u.slots.mezzanine_area_m2 == expected and u.changed == ["mezzanine_area_m2"]
    assert u.slots.land_area_m2 == 100 and u.slots.building_area_m2 == 80
    assert u.intent == "provide_info" and next_question(u.slots, more=True).slots == ["roof_type"]


@pytest.mark.parametrize("msg,expected", [("40", 40), ("40 m2", 40), ("khoảng 45m²", 45), ("lửng 50 m2", 50),
                                          ("khoảng 1/2 sàn", 40), ("bằng cả sàn", 80), ("diện tích lửng 35", 35)])
def test_mezzanine_answers_while_expected(msg, expected):
    s = _core(building_area_m2=80, basement=False, has_mezzanine=True)
    u = slot_filling.extract(msg, s, expected_slots=["mezzanine_area_m2"])
    assert u.slots.mezzanine_area_m2 == expected
    assert u.slots.land_area_m2 == 100 and u.slots.building_area_m2 == 80  # không đổi đất / diện tích xây dựng


def test_mezzanine_answer_ignores_llm_land_reading():
    """LLM đọc "40 m2" thành diện tích đất -> bỏ qua khi đang hỏi diện tích lửng."""
    s = _core(building_area_m2=80, basement=False, has_mezzanine=True)
    p = ScriptedProvider("", extracted={"land_area_m2": 40, "building_area_m2": 40, "intent": "provide_info"})
    u = slot_filling.extract("40 m2", s, p, expected_slots=["mezzanine_area_m2"])
    assert u.slots.mezzanine_area_m2 == 40 and u.slots.land_area_m2 == 100 and u.slots.building_area_m2 == 80


def test_mezzanine_area_in_free_text():
    assert parse_message("Tầng lửng 40 m2") == {"has_mezzanine": True, "mezzanine_area_m2": 40}
    assert parse_message("1 trệt 1 lửng 3 lầu, đất 5x20, 80m2 sàn").get("mezzanine_area_m2") is None
    u = slot_filling.extract("xây 80m2 trên đất 100m2, tầng lửng 30m2", Slots())
    assert (u.slots.land_area_m2, u.slots.building_area_m2, u.slots.mezzanine_area_m2) == (100, 80, 30)


def test_mezzanine_unknown_uses_disclosed_default():
    s = _core(building_area_m2=80, basement=False, has_mezzanine=True)
    u = slot_filling.extract("Tôi chưa biết diện tích tầng lửng", s, expected_slots=["mezzanine_area_m2"])
    assert u.slots.mezzanine_area_m2 is None and "mezzanine_area_m2" in u.slots.asked
    assert "mezzanine_area_m2" not in u.missing_required
    _, block = build_summary(u.slots.model_copy(update={"roof_type": "roof_concrete"}))
    assert ("Tạm tính: lửng bằng diện tích sàn (80 m²) — anh/chị bấm Sửa hoặc Cung cấp thêm thông tin để thay đổi."
            in block.assumptions)
    assert to_estimate_input(u.slots).mezzanine_area_m2 is None


def test_mezzanine_area_in_summary_and_estimate_input():
    s = _core(building_area_m2=80, basement=False, has_mezzanine=True, mezzanine_area_m2=40, roof_type="roof_concrete")
    text, block = build_summary(s)
    assert "lửng 40 m²" in text
    assert ("Tầng lửng", "Có (40 m²)") in [(i.label, i.value) for i in block.items]
    assert not any("tầng lửng" in a for a in block.assumptions)
    assert to_estimate_input(s).mezzanine_area_m2 == 40
    # Không có lửng -> không truyền diện tích lửng (engine coi diện tích lửng có giá trị là có lửng).
    assert to_estimate_input(s.model_copy(update={"has_mezzanine": False})).mezzanine_area_m2 is None


def test_no_mezzanine_clears_area_and_reasks_later():
    s = _core(building_area_m2=80, basement=False, has_mezzanine=True, mezzanine_area_m2=40,
              asked=["mezzanine_area_m2"])
    u = slot_filling.extract("không có lửng", s, expected_slots=["has_mezzanine"])
    assert u.slots.has_mezzanine is False and u.slots.mezzanine_area_m2 is None
    assert "mezzanine_area_m2" not in u.slots.asked
    u2 = slot_filling.extract("có tầng lửng", u.slots, expected_slots=["has_mezzanine"])
    assert next_question(u2.slots.model_copy(update={"roof_type": "roof_metal"}), more=True).slots == [
        "mezzanine_area_m2"]


def test_edit_mezzanine_area_question():
    s = _core(building_area_m2=80, has_mezzanine=True, mezzanine_area_m2=40)
    q = edit_field_question("Tôi muốn sửa diện tích lửng của nhà", s)
    assert q.slots == ["mezzanine_area_m2"] and q.text == "Dạ, tầng lửng rộng khoảng bao nhiêu m² ạ?"
    assert [o.label for o in q.quick_replies.options][:2] == ["Khoảng 1/2 sàn (40 m²)", "Bằng cả sàn (80 m²)"]


def test_budget_option_remove_mezzanine_clears_area():
    prev = _core(building_area_m2=80, has_mezzanine=True, mezzanine_area_m2=40, confirmed=True)
    u = orchestrator.apply_budget_option(prev, {"label_vi": "Bỏ tầng lửng",
                                                "changes": {"has_mezzanine": False, "mezzanine_area_m2": None}})
    assert u.slots.has_mezzanine is False and u.slots.mezzanine_area_m2 is None


@pytest.mark.db
def test_conversation_asks_mezzanine_area_and_estimates_with_it(db):
    sid = _new(db)
    r = _say(db, sid, "Tôi muốn xây nhà phố ở Thủ Đức, đất 5x20, 1 trệt 1 lửng 3 lầu, trọn gói")
    assert r.slots["has_mezzanine"] is True and [b.type for b in r.ui_blocks] == ["summary_confirm"]
    summary = next(b for b in r.ui_blocks if b.type == "summary_confirm")
    assert "lửng bằng diện tích sàn (100 m²)" in summary.assumptions[0]
    r = _say(db, sid, MORE_VALUE)
    assert question(r) == "Diện tích xây dựng mỗi tầng (m²)?" and (step(r).step, step(r).total) == (1, 4)
    r = _say(db, sid, "80 m2")
    assert question(r) == "Nhà có tầng hầm không?"
    r = _say(db, sid, "Không có tầng hầm")
    assert question(r) == "Loại mái dự kiến?"
    r = _say(db, sid, "Mái bằng bê tông")
    assert question(r) == "Diện tích tầng lửng (m²)?" and step(r).input_placeholder == "Nhập diện tích (m²)"
    assert (step(r).step, step(r).total) == (4, 4)
    r = _say(db, sid, step(r).options[0].value)  # "Khoảng 1/2 sàn (40 m²)"
    assert r.slots["mezzanine_area_m2"] == 40 and r.slots["land_area_m2"] == 100
    summary = next(b for b in r.ui_blocks if b.type == "summary_confirm")
    assert ("Tầng lửng", "Có (40 m²)", "Tôi muốn sửa diện tích lửng của nhà") in [
        (i.label, i.value, i.edit_value) for i in summary.items]
    r = _say(db, sid, "Đúng rồi, tính khái toán giúp tôi")
    est = next(res for name, res in r.tool_results if name == "calculate_estimate")
    mezz = [it for it in est.items if it.rule_code == "mezzanine"]
    assert len(mezz) == 1 and mezz[0].area_m2 == 40
    assert not any("chưa cung cấp" in a and "lửng" in a for a in est.assumptions)


@pytest.mark.db
def test_calculate_override_without_mezzanine_drops_slot_area(db):
    """"Nếu bỏ lửng thì sao": override has_mezzanine=False không được giữ diện tích lửng của slot."""
    sid = _new(db)
    slots = _core(building_area_m2=80, basement=False, has_mezzanine=True, mezzanine_area_m2=40,
                  roof_type="roof_concrete", confirmed=True)
    ctx = ToolContext(db=db, chat_session_id=sid, slots=slots, state=SessionState(), user_texts=[])
    o = execute_tool(ctx, "calculate_estimate", {"has_mezzanine": False})
    assert not o.is_error, o.error
    assert not [it for it in o.result.items if it.rule_code == "mezzanine"]
    o = execute_tool(ctx, "calculate_estimate", {})
    assert [it.area_m2 for it in o.result.items if it.rule_code == "mezzanine"] == [40]
    assert "mezzanine_area_m2" in tools.UpdateProjectRequirementsIn.model_fields
