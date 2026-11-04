"""Thông báo nhóm Lark khi khách gửi yêu cầu báo giá / liên hệ nhân viên (app/services/notify_service.py)."""
import base64
import hashlib
import hmac
import json

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import ChatSession, Contractor
from app.services import notify_service
from app.services.contact_service import ContactRequestIn, create_contact_request
from app.services.quote_service import QuoteRequestIn, create_quote_requests

HOOK = "https://open.larksuite.com/open-apis/bot/v2/hook/test-hook"
SLOTS = {"building_type": "nha_pho", "province": "TP. Hồ Chí Minh", "land_area_m2": 100, "floors": 4,
         "work_scope": "xay_tho"}


def _texts(card: dict) -> str:
    """Toàn bộ chữ trên thẻ (tiêu đề + trường + chú thích) để kiểm tra nội dung."""
    return json.dumps(card, ensure_ascii=False)


@pytest.fixture
def sent(monkeypatch):
    """Bật webhook (URL giả) và bắt payload thay vì gửi mạng thật; gửi đồng bộ để kiểm tra ngay."""
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(get_settings(), "lark_webhook_url", HOOK)
    monkeypatch.setattr(get_settings(), "lark_webhook_secret", "")
    monkeypatch.setattr(get_settings(), "admin_public_url", "http://localhost:8000/admin")
    monkeypatch.setattr(notify_service, "_post", lambda url, payload: calls.append((url, payload)))

    class _Now:
        def submit(self, fn, *a):
            fn(*a)

    monkeypatch.setattr(notify_service, "_executor", _Now())
    return calls


# ------------------------------------------------------------------ thuần
def test_disabled_without_webhook(monkeypatch):
    monkeypatch.setattr(get_settings(), "lark_webhook_url", "")
    called = []
    monkeypatch.setattr(notify_service, "_post", lambda *a: called.append(a))
    assert notify_service.send_card({"elements": []}) is False
    assert called == []


def test_signature_matches_lark_algorithm(sent, monkeypatch):
    monkeypatch.setattr(get_settings(), "lark_webhook_secret", "bi-mat")
    monkeypatch.setattr(notify_service.time, "time", lambda: 1_700_000_000)
    assert notify_service.send_card({"elements": []}) is True
    _, payload = sent[0]
    expected = base64.b64encode(hmac.new(b"1700000000\nbi-mat", b"", hashlib.sha256).digest()).decode()
    assert payload["timestamp"] == "1700000000" and payload["sign"] == expected
    assert payload["msg_type"] == "interactive"


def test_customer_text_is_plain_text_only():
    card = notify_service.contact_card(code="LH-1", name="<at id=all></at> **A**", phone="0901234567",
                                       note="[link](http://x)", contractor=None, project=None, source="composer",
                                       page_url=None, created_at=None)
    for f in card["elements"][0]["fields"]:
        assert f["text"]["tag"] == "plain_text"  # không lark_md -> không tag được cả nhóm / chèn link
    assert "<at id=all></at> **A**" in _texts(card)


def test_long_note_truncated_and_empty_shown_as_dash():
    card = notify_service.quote_card(code="DZ-1", name="A", phone="0901234567", email=None, note="x" * 5000,
                                     contractors=["BIC"], project=None, estimate=None, channel="Form yêu cầu báo giá")
    fields = [f["text"]["content"] for f in card["elements"][0]["fields"]]
    assert any(c.startswith("Email\n—") for c in fields)
    note = next(c for c in fields if c.startswith("Ghi chú gửi nhà thầu"))
    assert len(note) < 1100 and note.endswith("…")


# ------------------------------------------------------------------ luồng thật (DB)
@pytest.mark.db
def test_contact_request_notifies_with_all_fields(db, sent):
    chat = ChatSession(slots=SLOTS)
    db.add(chat)
    db.commit()
    c = db.scalars(select(Contractor).where(Contractor.is_active.is_(True)).order_by(Contractor.id)).first()
    res = create_contact_request(db, ContactRequestIn(
        customer_name="Nguyễn Văn A", customer_phone="+84 912 345 678", note="Gọi buổi chiều",
        chat_session_id=chat.id, contractor_id=c.id, source="contractor_card", page_url="https://dezon.vn/planning"))
    assert len(sent) == 1
    url, payload = sent[0]
    text = _texts(payload["card"])
    assert url == HOOK and payload["msg_type"] == "interactive"
    for needle in (res.code, "Nguyễn Văn A", "0912345678", "Gọi buổi chiều", c.name, "Thẻ nhà thầu",
                   "Nhà phố", "TP. Hồ Chí Minh", "Xây thô", "https://dezon.vn/planning", "Mở trang quản trị"):
        assert needle in text, needle
    # Gửi lại y hệt trong 10 phút -> không báo lại; có thông tin mới -> báo "Cập nhật".
    create_contact_request(db, ContactRequestIn(customer_name="Nguyễn Văn A", customer_phone="0912345678",
                                                note="Gọi buổi chiều", chat_session_id=chat.id, contractor_id=c.id))
    assert len(sent) == 1
    create_contact_request(db, ContactRequestIn(customer_name="Nguyễn Văn A", customer_phone="0912345678",
                                                note="Hỏi thêm về móng", chat_session_id=chat.id,
                                                contractor_id=c.id))
    assert len(sent) == 2 and "Cập nhật yêu cầu" in _texts(sent[1][1]["card"])
    assert "Hỏi thêm về móng" in _texts(sent[1][1]["card"])


@pytest.mark.db
def test_quote_request_notifies_with_all_fields(db, sent):
    chat = ChatSession(slots=SLOTS)
    db.add(chat)
    db.commit()
    cs = db.scalars(select(Contractor).where(Contractor.is_active.is_(True)).order_by(Contractor.id).limit(2)).all()
    res = create_quote_requests(db, QuoteRequestIn(
        contractor_ids=[c.id for c in cs], customer_name="Trần Thị B", customer_phone="0987654321",
        customer_email="b@example.com", note="Muốn khởi công tháng 3", chat_session_id=chat.id))
    assert len(sent) == 1
    text = _texts(sent[0][1]["card"])
    for needle in (res.code, "Trần Thị B", "0987654321", "b@example.com", "Muốn khởi công tháng 3",
                   *[c.name for c in cs], "Nhà phố", "Form yêu cầu báo giá", f"({len(cs)})"):
        assert needle in text, needle


@pytest.mark.db
def test_no_notification_when_not_configured(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "lark_webhook_url", "")
    called = []
    monkeypatch.setattr(notify_service, "send_card", lambda *a, **k: called.append(a))
    create_contact_request(db, ContactRequestIn(customer_name="A", customer_phone="0901234567"))
    assert called == []
