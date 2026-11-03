"""Review sửa lỗi — nhóm dialog (#3, #11, #12, #13, #14, #15, #25, #26)."""
import pytest

from app.ai import dialog, orchestrator, slot_filling
from app.ai.dialog import MORE_VALUE, build_summary
from app.ai.providers.mock import MockProvider
from app.ai.state import SessionState, split_raw, to_raw
from app.db.models import ChatSession
from app.schemas.common import BuildingType, WorkScope
from app.schemas.slots import Slots
from app.services import chat_service
from tests.wizard import question

CORE = "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô"
BUILDING_Q = "Diện tích xây dựng mỗi tầng"
BASEMENT_MEZZ_CLARIFY_TEXT = "Nhà có tầng hầm, tầng lửng hay cả hai?"
BASEMENT_Q = "Nhà có tầng hầm hoặc tầng lửng không?"
ROOF_Q = "Loại mái dự kiến?"
CONFIRM = "Đúng rồi, tính khái toán giúp tôi"


def _core_slots(**kw) -> Slots:
    base = dict(building_type=BuildingType.nha_pho, province="TP. Hồ Chí Minh", district="Thủ Đức",
                land_width_m=5, land_length_m=20, land_area_m2=100, floors=4, work_scope=WorkScope.xay_tho)
    return Slots(**{**base, **kw})


def _new(db):
    return chat_service.create_session(db).session_id


def _say(db, sid, msg, provider=None):
    return orchestrator.handle_message(db, sid, msg, provider=provider or MockProvider())


def _types(r):
    return [b.type for b in r.ui_blocks]


def _shown(r) -> str:
    """Văn bản + câu hỏi trên thẻ question_step (câu hỏi slot nằm trên thẻ, không lặp trong văn bản)."""
    return r.text + "\n" + question(r)


def _summary(r):
    return next(b for b in r.ui_blocks if b.type == "summary_confirm")


# =============================================================================================
# #13 — câu trả lời ngắn cho câu hỏi mái
# =============================================================================================
@pytest.mark.parametrize("msg,roof", [
    ("tôn", "roof_metal"), ("Tôn ạ", "roof_metal"), ("lợp tôn", "roof_metal"), ("tôn lạnh", "roof_metal"),
    ("ngói", "roof_tile_on_concrete"), ("ngói đổ bê tông", "roof_tile_on_concrete"),
    ("ngói kèo thép", "roof_tile_on_truss"), ("mái thái", "roof_tile_on_truss"), ("Mái Nhật", "roof_tile_on_truss"),
    ("bê tông", "roof_concrete"), ("BTCT", "roof_concrete"), ("đổ bê tông", "roof_concrete"),
    ("mái bằng", "roof_concrete"), ("bê tông cốt thép", "roof_concrete"),
])
def test_short_roof_answers(msg, roof):
    u = slot_filling.extract(msg, _core_slots(), expected_slots=["roof_type"])
    assert u.slots.roof_type == roof
    assert u.intent == "provide_info"


def test_short_roof_only_when_roof_is_asked():
    u = slot_filling.extract("tôn", _core_slots(), expected_slots=["building_area_m2"])
    assert u.slots.roof_type is None


def test_unrecognised_detail_answer_is_asked_again_not_defaulted():
    u = slot_filling.extract("để tôi xem bản vẽ đã", _core_slots(), expected_slots=["roof_type"])
    assert u.slots.roof_type is None and "roof_type" not in u.slots.asked
    assert not dialog.key_done(u.slots, "roof")  # nhóm câu hỏi thêm hỏi lại câu mái
    # "chưa biết" -> dùng mặc định có nêu rõ
    u = slot_filling.extract("Tôi chưa biết loại mái", _core_slots(), expected_slots=["roof_type"])
    assert "roof_type" in u.slots.asked and dialog.key_done(u.slots, "roof")


def test_llm_concrete_roof_kept_when_message_says_be_tong():
    class Stub:
        name = "stub"

        def extract(self, system, text, schema):
            return {"roof_type": "roof_concrete", "intent": "provide_info"}

    u = slot_filling.extract("làm bê tông cho chắc", _core_slots(), provider=Stub(), expected_slots=["roof_type"])
    assert u.slots.roof_type == "roof_concrete"


# =============================================================================================
# #12 — "không" cho câu hỏi mái không phải ý muốn sửa
# =============================================================================================
def test_bare_no_to_roof_question_is_not_edit():
    u = slot_filling.extract("không", _core_slots(basement=False, has_mezzanine=False,
                                                  asked=["building_area_m2"]), expected_slots=["roof_type"])
    assert u.intent != "edit"
    assert "roof_type" in u.slots.asked and u.missing_required == []


@pytest.mark.db
def test_bare_no_to_roof_question_shows_summary(db):
    sid = _new(db)
    _say(db, sid, CORE)
    _say(db, sid, MORE_VALUE)
    _say(db, sid, "80")
    r = _say(db, sid, "không")
    assert ROOF_Q in _shown(r)
    r = _say(db, sid, "không")  # hết câu hỏi thêm -> bản tóm tắt
    assert "summary_confirm" in _types(r) and "muốn sửa thông tin nào" not in r.text
    assert any("mái bê tông cốt thép" in a for a in _summary(r).assumptions)


# =============================================================================================
# #11 — "có" cho câu hỏi chung hầm/lửng
# =============================================================================================
def test_bare_yes_to_combined_basement_mezz_not_marked_asked():
    u = slot_filling.extract("có ạ", _core_slots(building_area_m2=80, asked=["building_area_m2"]),
                             expected_slots=["basement", "has_mezzanine"])
    assert u.slots.basement is None and u.slots.has_mezzanine is None
    assert "basement" not in u.slots.asked and "has_mezzanine" not in u.slots.asked
    # Hỏi riêng một mục thì "có" rõ nghĩa.
    u = slot_filling.extract("có", _core_slots(), expected_slots=["basement"])
    assert u.slots.basement is True


@pytest.mark.db
def test_bare_yes_to_combined_question_asks_which(db):
    sid = _new(db)
    _say(db, sid, CORE)
    _say(db, sid, MORE_VALUE)
    r = _say(db, sid, "80")
    assert BASEMENT_Q in _shown(r)
    r = _say(db, sid, "có")
    assert BASEMENT_MEZZ_CLARIFY_TEXT in _shown(r)
    labels = [o.label for o in r.ui_blocks[-1].options]
    assert "Không hầm, không lửng" not in labels and "Có tầng lửng" in labels
    assert r.slots["basement"] is None and r.slots["has_mezzanine"] is None
    r = _say(db, sid, "Có tầng lửng, không hầm")
    assert r.slots["has_mezzanine"] is True and r.slots["basement"] is False
    assert ROOF_Q in _shown(r)  # mái trước, rồi diện tích lửng
    r = _say(db, sid, "Mái tôn")
    assert question(r) == "Diện tích tầng lửng (m²)?"
    r = _say(db, sid, "Tầng lửng 40 m2")
    assert r.slots["mezzanine_area_m2"] == 40 and "summary_confirm" in _types(r)


@pytest.mark.db
def test_bare_yes_in_edit_flow_asks_which(db):
    sid = _new(db)
    _say(db, sid, CORE + ", xây kín đất, không hầm, không lửng, mái bằng")
    r = _say(db, sid, "Tôi muốn sửa thông tin hầm, lửng của nhà")
    assert question(r) == BASEMENT_Q
    r = _say(db, sid, "có")
    assert BASEMENT_MEZZ_CLARIFY_TEXT in _shown(r) and "summary_confirm" not in _types(r)


# =============================================================================================
# #3 — câu hỏi/giả định/tìm nhà thầu khi đang hỏi chi tiết không ghi đè thông tin công trình
# =============================================================================================
@pytest.mark.db
def test_hypothetical_during_detail_questions_keeps_floors_and_reasks(db):
    sid = _new(db)
    _say(db, sid, CORE)
    r = _say(db, sid, MORE_VALUE)
    assert BUILDING_Q in _shown(r)
    r = _say(db, sid, "Nếu chỉ xây 3 tầng thì hết bao nhiêu?")
    assert r.slots["floors"] == 4
    assert BUILDING_Q in _shown(r)  # câu hỏi chi tiết đang chờ được hỏi lại
    sess = db.get(ChatSession, sid)
    assert "building_area_m2" not in split_raw(sess.slots)[0].asked


@pytest.mark.db
def test_find_contractor_during_detail_questions_keeps_province(db):
    sid = _new(db)
    _say(db, sid, CORE)
    _say(db, sid, MORE_VALUE)
    r = _say(db, sid, "80")
    assert BASEMENT_Q in _shown(r)
    r = _say(db, sid, "Tìm nhà thầu tại Hà Nội")
    assert r.slots["province"] == "TP. Hồ Chí Minh" and r.slots["district"] == "TP. Thủ Đức"
    assert BASEMENT_Q in _shown(r)


def test_question_answering_pending_detail_is_kept():
    prev = _core_slots(building_area_m2=80, basement=False, has_mezzanine=False,
                       asked=["building_area_m2", "basement", "has_mezzanine"])
    u = slot_filling.extract("mái tôn được không?", prev, expected_slots=["roof_type"])
    out = orchestrator._protect_profile(prev, u, "mái tôn được không?", ["roof_type"])
    assert out.slots.roof_type == "roof_metal"


# =============================================================================================
# #14 — sửa diện tích đất sau "xây kín đất"
# =============================================================================================
def test_full_lot_building_area_follows_land_edit():
    prev = _core_slots(building_area_m2=100, confirmed=False, asked=["building_area_m2"])
    u = slot_filling.extract("4x15", prev, expected_slots=["land_area_m2"])
    assert u.slots.land_area_m2 == 60 and u.slots.building_area_m2 == 60
    assert "building_area_m2" in u.changed


def test_building_area_larger_than_new_land_is_asked_again():
    prev = _core_slots(building_area_m2=90, asked=["building_area_m2"])
    u = slot_filling.extract("4x15", prev, expected_slots=["land_area_m2"])
    assert u.slots.land_area_m2 == 60 and u.slots.building_area_m2 is None
    assert "building_area_m2" not in u.slots.asked and "building_area_m2" in dialog.more_slots(u.slots)


def test_building_area_within_new_land_is_kept():
    prev = _core_slots(building_area_m2=50, asked=["building_area_m2"])
    u = slot_filling.extract("4x15", prev, expected_slots=["land_area_m2"])
    assert u.slots.building_area_m2 == 50


def test_summary_warns_when_building_area_exceeds_land():
    s = _core_slots(land_width_m=None, land_length_m=None, land_area_m2=60, building_area_m2=100,
                    basement=False, has_mezzanine=False, roof_type="roof_concrete")
    text, block = build_summary(s)
    assert "lớn hơn diện tích đất" in text
    assert block.assumptions[0].startswith("Diện tích xây dựng mỗi tầng (100 m²) lớn hơn diện tích đất (60 m²)")
    apt = s.model_copy(update={"building_type": BuildingType.can_ho})
    assert "lớn hơn diện tích đất" not in build_summary(apt)[0]


@pytest.mark.db
def test_edit_land_after_full_lot_updates_summary(db):
    sid = _new(db)
    r = _say(db, sid, CORE + ", xây kín đất, không hầm, không lửng, mái bằng")
    assert {i.label: i.value for i in _summary(r).items}["Diện tích xây dựng mỗi tầng"] == "100 m²"
    _say(db, sid, "Tôi muốn sửa diện tích đất")
    r = _say(db, sid, "4x15")
    items = {i.label: i.value for i in _summary(r).items}
    assert items["Diện tích xây dựng mỗi tầng"] == "60 m²"
    r = _say(db, sid, CONFIRM)
    table = next(b for b in r.ui_blocks if b.type == "estimate_table").data
    assert table.input.building_area_m2 == 60


# =============================================================================================
# #15 — LLM không đưa lại diện tích đất khi khách trả lời diện tích xây dựng
# =============================================================================================
class _LandLLM:
    name = "stub"

    def __init__(self, raw):
        self.raw = raw

    def extract(self, system, text, schema):
        return {**self.raw, "intent": "provide_info"}


@pytest.mark.parametrize("msg", ["80 m2", "80"])
def test_llm_land_ignored_when_answering_building_area(msg):
    prev = _core_slots()
    u = slot_filling.extract(msg, prev, provider=_LandLLM({"land_area_m2": 80}), expected_slots=["building_area_m2"])
    assert u.slots.building_area_m2 == 80
    assert u.slots.land_area_m2 == 100 and u.slots.land_width_m == 5 and u.slots.land_length_m == 20
    assert u.intent == "provide_info"


def test_llm_land_still_used_when_customer_mentions_land():
    prev = _core_slots()
    u = slot_filling.extract("đất 120 m2", prev, provider=_LandLLM({"land_area_m2": 120}),
                             expected_slots=["building_area_m2"])
    assert u.slots.land_area_m2 == 120


# =============================================================================================
# #25 — "đất 100m2, xây 80m2" trong cùng một tin
# =============================================================================================
@pytest.mark.parametrize("msg", ["đất 100m2, xây 80m2", "xây 80m2 trên đất 100m2", "Đất 100 m2 xây khoảng 80 m2"])
def test_land_and_building_in_one_message(msg):
    prev = _core_slots(land_width_m=None, land_length_m=None)
    u = slot_filling.extract(msg, prev, expected_slots=["building_area_m2"])
    assert u.slots.building_area_m2 == 80 and u.slots.land_area_m2 == 100


def test_land_and_building_in_first_message():
    u = slot_filling.extract("nhà phố đất 100m2, xây 80m2, 3 tầng", Slots())
    assert u.slots.land_area_m2 == 100 and u.slots.building_area_m2 == 80
    u = slot_filling.extract("nhà phố 5x20, xây 80m2", Slots())
    assert u.slots.land_area_m2 == 100 and u.slots.building_area_m2 == 80


def test_bare_xay_area_alone_is_still_land():
    u = slot_filling.extract("tôi muốn xây 100m2", Slots())
    assert u.slots.land_area_m2 == 100 and u.slots.building_area_m2 is None


# =============================================================================================
# #26 — phiên đã xác nhận trước khi có câu hỏi chi tiết
# =============================================================================================
@pytest.mark.db
def test_legacy_confirmed_session_without_details_keeps_confirmation(db):
    """Phiên đã xác nhận khi chưa nói chi tiết: chi tiết không còn là bắt buộc -> giữ xác nhận, tính lại khái toán
    (hash cũ không khớp) với mặc định có nêu rõ; không hỏi lại chi tiết."""
    sid = _new(db)
    sess = db.get(ChatSession, sid)
    legacy = _core_slots(confirmed=True)
    state = SessionState(expected_slots=[], estimate={"id": "legacy-est", "cost_min": 1, "cost_max": 2,
                                                      "cost_display": "x", "slots_hash": "old"},
                         matched_for="legacy-est:0", budget_checked_for="legacy-est:0")
    sess.slots = to_raw(legacy, state)
    db.commit()

    r = _say(db, sid, "so sánh 2 nhà thầu đầu")
    assert r.slots["confirmed"] is True and r.stage != "collect"
    assert "calculate_estimate" in r.tool_calls and "question_step" not in _types(r)
    _, st = split_raw(db.get(ChatSession, sid).slots)
    assert st.estimate is not None and st.estimate.id != "legacy-est"
