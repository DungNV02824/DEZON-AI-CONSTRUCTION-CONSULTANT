"""Chỉ số mục 12 (metrics_service + GET /api/admin/metrics): DB gần rỗng và khi có dữ liệu."""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.config import get_settings
from app.db.models import (
    AnalyticsEvent,
    ChatMessage,
    ChatSession,
    Contractor,
    ContractorQuote,
    DataSource,
    GuardrailViolation,
    QuoteRequest,
)
from app.services.metrics_service import compute_metrics

TOKEN = "metrics-token"
H = {"X-Admin-Token": TOKEN}
HCM = "TP. Hồ Chí Minh"
ESTIMATE = {"building_type": "nha_pho", "province": HCM, "land_area_m2": 100, "floors": 4,
            "foundation_type": "foundation_pile", "roof_type": "roof_concrete", "work_scope": "xay_tho"}
KEYS = ["estimate_completion_rate", "avg_turns_to_complete", "estimate_accuracy_in_range", "estimate_median_error",
        "contractor_selection_by_rank", "quote_request_rate", "guardrail_violation_rate", "coverage_price",
        "coverage_experience", "coverage_verified_fields"]


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_token", TOKEN)


def test_metrics_requires_token(client):
    assert client.get("/api/admin/metrics").status_code == 401


def test_metrics_structure_on_nearly_empty_db(client):
    r = client.get("/api/admin/metrics", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert list(body["metrics"]) == KEYS
    assert body["generated_at"]
    for key, m in body["metrics"].items():
        assert m["key"] == key and m["label_vi"] and m["note"]
        assert {"value", "unit", "numerator", "denominator", "sample_size", "breakdown"} <= set(m)
    no_data = KEYS[:7]
    for key in no_data:
        assert body["metrics"][key]["value"] is None, key
        assert "Chưa có dữ liệu" in body["metrics"][key]["note"], key
    # Seed có 3 nhà thầu: Kiến Phong có đơn giá + kinh nghiệm, 2 hồ sơ còn lại thiếu
    assert body["metrics"]["coverage_price"]["denominator"] == 3
    assert body["metrics"]["coverage_price"]["value"] == pytest.approx(1 / 3, abs=1e-3)
    assert body["metrics"]["coverage_experience"]["value"] == pytest.approx(1 / 3, abs=1e-3)
    assert body["metrics"]["coverage_verified_fields"]["value"] == 0
    assert set(body["totals"]) >= {"chat_sessions", "estimate_sessions", "recommendation_logs", "quote_requests"}


def test_metrics_without_contractors(db):
    db.execute(update(Contractor).values(is_active=False))
    rep = compute_metrics(db)
    for key in ("coverage_price", "coverage_experience", "coverage_verified_fields"):
        assert rep.metrics[key].value is None and "Chưa có dữ liệu" in rep.metrics[key].note


def test_metrics_with_data(client, db):
    chat1, chat2 = ChatSession(id=uuid.uuid4()), ChatSession(id=uuid.uuid4())
    db.add_all([chat1, chat2])
    db.flush()
    est = client.post("/api/estimates", json={**ESTIMATE, "chat_session_id": str(chat1.id)}).json()
    assert est["cost_min"] and est["cost_max"]
    # 4 câu trả lời trợ lý, 1 vi phạm
    db.add_all([ChatMessage(session_id=chat1.id, role="assistant", content="x") for _ in range(3)]
               + [ChatMessage(session_id=chat2.id, role="assistant", content="y"),
                  ChatMessage(session_id=chat2.id, role="user", content="z")])
    db.add(GuardrailViolation(session_id=chat1.id, rule="untraceable_number", detail={"text": "9 tỷ"}))
    db.add_all([AnalyticsEvent(session_id=chat1.id, event="slots_completed", props={"turns": 3}),
                AnalyticsEvent(session_id=chat2.id, event="slots_completed", props={"turns": 5}),
                AnalyticsEvent(session_id=chat2.id, event="slots_completed", props={})])
    db.flush()

    m = client.post("/api/contractors/match", json={"province": HCM, "building_type": "nha_pho",
                                                    "estimate_session_id": est["estimate_session_id"],
                                                    "chat_session_id": str(chat1.id)}).json()
    shown = len(m["results"])
    first, second = m["results"][0]["contractor"]["id"], m["results"][1]["contractor"]["id"]
    assert client.post(f"/api/recommendations/{m['recommendation_log_id']}/click",
                       json={"contractor_id": first}).status_code == 200
    q = client.post("/api/quote-requests", json={"contractor_ids": [second, first],
                                                 "estimate_session_id": est["estimate_session_id"],
                                                 "customer_name": "Chị B", "customer_phone": "0912345678"})
    assert q.status_code == 201
    qr_ids = [uuid.UUID(i) for i in q.json()["ids"]]
    mid = (est["cost_min"] + est["cost_max"]) / 2
    over = est["cost_max"] * 1.2
    db.add_all([ContractorQuote(quote_request_id=qr_ids[0], contractor_id=second, total_amount=Decimal(int(mid))),
                ContractorQuote(quote_request_id=qr_ids[1], contractor_id=first, total_amount=Decimal(int(over))),
                ContractorQuote(quote_request_id=qr_ids[1], contractor_id=first, total_amount=None)])
    # Xác minh nguồn hồ sơ Kiến Phong -> có trường verified
    kp = db.scalar(select(Contractor).where(Contractor.slug == "kien-phong"))
    db.get(DataSource, kp.source_id).status = "verified"
    db.flush()

    r = client.get("/api/admin/metrics", headers=H)
    assert r.status_code == 200, r.text
    mt = r.json()["metrics"]
    assert mt["estimate_completion_rate"]["value"] == 0.5
    assert (mt["estimate_completion_rate"]["numerator"], mt["estimate_completion_rate"]["denominator"]) == (1, 2)
    assert mt["avg_turns_to_complete"]["value"] == 4 and mt["avg_turns_to_complete"]["sample_size"] == 2
    acc = mt["estimate_accuracy_in_range"]
    assert acc["value"] == 0.5 and acc["sample_size"] == 2 and "Bỏ qua 1" in acc["note"]
    expected_err = ((over - mid) / mid) / 2  # trung vị của [0, (over-mid)/mid]
    assert mt["estimate_median_error"]["value"] == pytest.approx(expected_err, abs=1e-3)
    sel = mt["contractor_selection_by_rank"]
    assert sel["numerator"] == 1 and sel["denominator"] == shown
    by_rank = {b["rank"]: b for b in sel["breakdown"]}
    assert by_rank[1]["clicked"] == 1 and by_rank[1]["click_rate"] == 1.0
    assert by_rank[1]["quoted"] == 1 and by_rank[2]["quoted"] == 1 and by_rank[2]["clicked"] == 0
    qrr = mt["quote_request_rate"]
    assert qrr["value"] == 1.0 and qrr["breakdown"][0]["quote_requests"] == 2
    assert qrr["breakdown"][0]["recommendation_logs"] == 1
    g = mt["guardrail_violation_rate"]
    assert g["value"] == 0.25 and g["breakdown"] == [{"rule": "untraceable_number", "count": 1}]
    assert mt["coverage_verified_fields"]["value"] > 0
    totals = r.json()["totals"]
    assert totals["chat_sessions"] == 2 and totals["contractor_quotes"] == 3


def test_quote_linked_through_chat_session_event(client, db):
    chat = ChatSession(id=uuid.uuid4())
    db.add(chat)
    db.flush()
    m = client.post("/api/contractors/match", json={"province": HCM, "building_type": "nha_pho",
                                                    "chat_session_id": str(chat.id)}).json()
    cid = m["results"][2]["contractor"]["id"]
    db.add(AnalyticsEvent(session_id=chat.id, event="quote_request_created", props={"contractor_ids": [cid]}))
    db.add(QuoteRequest(id=uuid.uuid4(), contractor_id=cid, customer_name="A", customer_phone="0912345678"))
    db.flush()
    rep = compute_metrics(db)
    assert rep.metrics["quote_request_rate"].value == 1.0
    rank3 = next(b for b in rep.metrics["contractor_selection_by_rank"].breakdown if b["rank"] == 3)
    assert rank3["quoted"] == 1
