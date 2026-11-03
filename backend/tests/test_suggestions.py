"""Gợi ý câu hỏi tiếp theo cuối lượt (app/ai/suggestions.py + orchestrator)."""
from concurrent.futures import Future

import pytest

from app.ai import orchestrator, suggestions
from app.ai.dialog import CONFIRM_VALUE
from app.ai.providers.mock import MockProvider
from app.ai.suggestions import SuggestContext
from app.services import chat_service

HCM = "TP. Hồ Chí Minh"
FULL = "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô, xây kín đất, không hầm, không lửng, mái bằng"


def _ctx(**kw) -> SuggestContext:
    base = dict(building_type="nha_pho", province=HCM, floors=4, work_scope="xay_tho",
                contractors=["Kiến Phong", "Smartcons Tech", "BIC"], last_user_message="2–4 tỷ", seed="s:1")
    return SuggestContext(**{**base, **kw})


def _done(value) -> Future:
    f: Future = Future()
    f.set_result(value)
    return f


def _failed() -> Future:
    f: Future = Future()
    f.set_exception(RuntimeError("LLM lỗi"))
    return f


# --------------------------------------------------------------------------------------------- thuần
def test_template_candidates_follow_context():
    c = suggestions.template_candidates(_ctx())
    assert "Hồ sơ của Kiến Phong có những công trình nào?" in c
    assert "Kiến Phong và Smartcons Tech khác nhau thế nào?" in c
    assert "Nếu chọn trọn gói thì chi phí khoảng bao nhiêu?" in c  # đang xây thô -> gợi ý trọn gói
    assert "Nếu xây 5 tầng thì chi phí thay đổi thế nào?" in c
    assert f"Đơn giá xây thô nhà phố tại {HCM} hiện khoảng bao nhiêu?" in c
    c2 = suggestions.template_candidates(_ctx(work_scope="tron_goi"))
    assert "Nếu chỉ xây thô thì chi phí khoảng bao nhiêu?" in c2


def test_pick_filters_and_is_stable_per_seed():
    bad = ["Nhà thầu nào tốt nhất?", "So sánh các nhà thầu", "Yêu cầu báo giá", "ngan sach bao nhieu",
           "ok", "2–4 tỷ", "Câu hỏi\nhai dòng nhé"]
    good = ["Hồ sơ của BIC có những công trình nào?", "Khái toán này chưa bao gồm những chi phí nào?",
            "Đơn giá xây thô nhà phố hiện khoảng bao nhiêu?", "Còn nhà thầu nào khác tại TP. Hồ Chí Minh không?"]
    out = suggestions.pick(bad + good, _ctx(), k=3)
    assert len(out) == 3 and set(out) <= set(good)
    assert suggestions.pick(bad + good, _ctx(), k=3) == out  # cùng phiên + lượt -> cùng gợi ý
    assert suggestions.pick(good + good, _ctx(seed="x"), k=10) == good  # bỏ trùng


def test_build_uses_llm_suggestions_then_tops_up_with_templates():
    llm = ["Kiến Phong đã làm nhà phố 4 tầng nào chưa?", "Nhà thầu nào tốt nhất?"]
    block = suggestions.build(_ctx(), _done(llm))
    labels = [o.label for o in block.options]
    assert block.type == "suggestions" and block.title == "Gợi ý cho anh/chị"
    assert len(labels) == 5 and labels[0] == llm[0] and "Nhà thầu nào tốt nhất?" not in labels
    assert all(o.value == o.label for o in block.options)


@pytest.mark.parametrize("future", [None, "failed"])
def test_build_falls_back_to_templates(future):
    block = suggestions.build(_ctx(), _failed() if future == "failed" else None)
    templates = set(suggestions.template_candidates(_ctx()))
    assert len(block.options) == 5 and {o.label for o in block.options} <= templates


def test_mock_provider_does_not_call_llm():
    assert suggestions.start(MockProvider(), _ctx()) is None


# --------------------------------------------------------------------------------------------- luồng chat
def _say(db, sid, msg):
    return orchestrator.handle_message(db, sid, msg, provider=MockProvider())


@pytest.mark.db
def test_suggestions_only_after_main_flow_and_not_while_asking(db):
    sid = chat_service.create_session(db).session_id
    r = _say(db, sid, FULL)
    assert "suggestions" not in [b.type for b in r.ui_blocks]  # đang xác nhận
    r = _say(db, sid, CONFIRM_VALUE)
    types = [b.type for b in r.ui_blocks]
    assert "contractor_list" in types and r.text.endswith(orchestrator.BUDGET_TAIL)
    # Ngay sau "Tính ngay": nút ngân sách giữ nguyên, gợi ý ở cuối tin nhắn.
    assert types[-2:] == ["quick_replies", "suggestions"] and len(r.ui_blocks[-1].options) == 5
    budget = next(b for b in r.ui_blocks if b.type == "quick_replies")
    r = _say(db, sid, budget.options[1].value)
    types = [b.type for b in r.ui_blocks]
    assert types[-1] == "suggestions"  # luôn ở cuối tin nhắn
    sugg = r.ui_blocks[-1]
    assert len(sugg.options) == 5 and all("tốt nhất" not in o.label for o in sugg.options)
    # Provider mock -> câu mẫu theo đúng ngữ cảnh phiên (nhà phố, TP.HCM, nhà thầu vừa đề xuất).
    contractors = [c.contractor.name for c in next(b for b in r.ui_blocks if b.type == "contractor_list").data.results] \
        if "contractor_list" in types else []
    ctx = SuggestContext(building_type="nha_pho", province=HCM, floors=4, work_scope="xay_tho",
                         contractors=contractors)
    if contractors:
        assert {o.label for o in sugg.options} <= set(suggestions.template_candidates(ctx))
    # Câu hỏi tự do sau đó (không chờ trả lời) -> vẫn có gợi ý ở cuối.
    r = _say(db, sid, "Khái toán này chưa bao gồm những chi phí nào?")
    assert r.ui_blocks and r.ui_blocks[-1].type == "suggestions"
