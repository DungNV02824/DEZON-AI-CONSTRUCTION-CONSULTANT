import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db.models import Contractor, QuoteRequest
from app.schemas.estimate import EstimateInput
from app.services import estimate_service
from app.services.quote_service import (
    QuoteRequestError,
    QuoteRequestIn,
    create_quote_requests,
    normalize_phone,
)


@pytest.mark.parametrize("raw,expected", [("0912 345 678", "0912345678"), ("+84912345678", "0912345678"),
                                          ("84.912.345.678", "0912345678"), ("02838123456", "02838123456")])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["12345", "abc", "", "0912"])
def test_normalize_phone_invalid(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_quote_request_in_validation():
    with pytest.raises(ValidationError):
        QuoteRequestIn(contractor_ids=[], customer_name="A", customer_phone="0912345678")
    with pytest.raises(ValidationError):
        QuoteRequestIn(contractor_ids=[1], customer_name="A", customer_phone="0912345678", customer_email="x@")
    q = QuoteRequestIn(contractor_ids=[1, 1, 2], customer_name="A", customer_phone="+84912345678",
                       customer_email=" ")
    assert q.contractor_ids == [1, 2] and q.customer_email is None


def test_create_quote_requests_with_estimate(db):
    est = estimate_service.create_estimate(db, EstimateInput(
        building_type="nha_pho", province="TP. Hồ Chí Minh", land_area_m2=100, floors=4, work_scope="xay_tho"))
    ids = list(db.scalars(select(Contractor.id).order_by(Contractor.id)).all())[:2]
    res = create_quote_requests(db, QuoteRequestIn(contractor_ids=ids, estimate_session_id=est.estimate_session_id,
                                                   customer_name="Nguyễn Văn A", customer_phone="0912345678"))
    assert len(res.ids) == 2 and res.code.startswith("DZ-")
    rows = db.scalars(select(QuoteRequest).where(QuoteRequest.id.in_(res.ids))).all()
    assert all(r.estimate_session_id == est.estimate_session_id and r.status == "new" for r in rows)


def test_create_quote_requests_unknown_contractor(db):
    with pytest.raises(QuoteRequestError):
        create_quote_requests(db, QuoteRequestIn(contractor_ids=[999999], customer_name="A",
                                                 customer_phone="0912345678"))
