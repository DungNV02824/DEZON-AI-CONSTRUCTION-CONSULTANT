"""K1: tool update_project_requirements không được nhận số LLM bịa vào slot (slot là nguồn hợp lệ của validator)."""
from app.ai.slot_filling import update_slots
from app.ai.state import SessionState
from app.ai.tools import ToolContext, UpdateProjectRequirementsIn, _update_requirements
from app.schemas.slots import Slots

MSG = "nhà phố 5x20 Thủ Đức 1 trệt 3 lầu xây thô"


def test_update_slots_rejects_values_not_in_customer_text():
    u = update_slots(Slots(), {"land_area_m2": 250, "budget_vnd": 3_000_000_000, "province": "Hà Nội"}, MSG)
    assert u.changed == []


def test_update_slots_accepts_values_derived_from_customer_text():
    u = update_slots(Slots(), {"land_width_m": 5, "land_length_m": 20, "land_area_m2": 100, "floors": 4}, MSG)
    assert u.slots.land_area_m2 == 100 and u.slots.floors == 4


def test_tool_handler_filters_against_session_user_texts(db):
    ctx = ToolContext(db=db, chat_session_id=None, slots=Slots(), state=SessionState(), user_texts=[MSG])
    out = _update_requirements(ctx, UpdateProjectRequirementsIn(land_area_m2=250, floors=4))
    assert ctx.slots.land_area_m2 is None and ctx.slots.floors == 4
    assert "land_area_m2" not in out["changed"]
