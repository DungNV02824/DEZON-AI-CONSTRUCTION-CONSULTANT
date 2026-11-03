"""Test service nhà thầu với DB seed dev (Kiến Phong, Smartcons Tech, Huy Hoàng — mục 11.3)."""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import Contractor, EstimateSession, RecommendationLog
from app.formatting import NO_DATA
from app.schemas.matching import FIT_REVIEW, MatchCriteria
from app.services import contractor_service as svc

HCM = "TP. Hồ Chí Minh"


def _id(db, slug: str) -> int:
    return db.scalar(select(Contractor.id).where(Contractor.slug == slug))


def test_profile_completeness_computed_by_seed_hook(db):
    vals = {c.slug: c.profile_completeness for c in db.scalars(select(Contractor))}
    assert vals["kien-phong"] == Decimal("0.900")
    assert vals["smartcons-tech"] == Decimal("0.300")
    assert vals["huy-hoang"] == Decimal("0.200")
    assert svc.recompute_profile_completeness(db) == 0  # đã đúng -> không thay đổi


def test_build_contractor_card_evidence(db):
    kp = db.get(Contractor, _id(db, "kien-phong"))
    card = svc.build_contractor_card(db, kp)
    ev = card.evidence
    assert ev["building_types"].display == "Nhà phố · Biệt thự · Căn hộ"
    assert ev["years_experience"].display == "8 năm"
    assert ev["founded_year"].display == "2018"
    assert ev["staff"].display == "20 thợ, 12 văn phòng"
    assert ev["rough_price"].display == "4,3–4,7 triệu/m²"
    assert ev["finish_price"].display == "7,3–9,5 triệu/m²"
    assert ev["typical_project_value"].display == "25 tỷ/công trình"
    assert ev["dezon_project_count"].display == "7 công trình ghi nhận trên Dezon"
    assert ev["warranty"].display == NO_DATA
    assert ev["rough_price"].status == "on_dezon"
    assert ev["rough_price"].source.url == kp.profile_url
    assert str(ev["rough_price"].source.updated_at) == "2026-10-31"
    assert 1 <= len(card.cover_images) <= svc.MAX_COVER_IMAGES


def test_match_nha_pho_hcm_returns_three_and_logs(db):
    res = svc.match_contractors(db, MatchCriteria(province=HCM, building_type="nha_pho", work_scope="xay_tho",
                                                  limit=5))
    assert len(res.results) == 3 and res.total_candidates == 3 and not res.relaxed_to_neighbors
    assert res.weights_version == 1 and res.recommendation_log_id
    names = [r.contractor.name for r in res.results]
    assert names[0] == "Kiến Phong" and set(names) == {"Kiến Phong", "Smartcons Tech", "Huy Hoàng"}
    for r in res.results:
        assert len(r.breakdown) == 6
        assert len(r.reasons) >= 2
        for reason in r.reasons:
            assert r.contractor.evidence[reason.evidence].has_value
            assert "tốt nhất" not in reason.text.lower()
    by_name = {r.contractor.name: r for r in res.results}
    assert by_name["Smartcons Tech"].fit_label == FIT_REVIEW
    assert by_name["Huy Hoàng"].fit_label == FIT_REVIEW
    assert "Chưa có thông tin đơn giá — cần đối chiếu" in [c.text for c in by_name["Huy Hoàng"].cautions]

    log = db.get(RecommendationLog, res.recommendation_log_id)
    assert log.weights_version == 1 and log.criteria["province"] == HCM
    assert len(log.results) == 3
    first = log.results[0]
    assert {"contractor_id", "score", "breakdown", "reasons", "rank"} <= set(first)
    assert len(first["breakdown"]) == 6 and first["rank"] == 1


def test_match_with_estimate_session_and_budget(db):
    es = EstimateSession(input={"building_type": "nha_pho", "province": HCM, "floors": 4, "work_scope": "xay_tho",
                                "land_area_m2": 100}, rules_version=1, converted_area_m2=Decimal("490"),
                         cost_min=Decimal("2058000000"), cost_max=Decimal("2352000000"), assumptions=[])
    db.add(es)
    db.flush()
    res = svc.match_contractors(db, MatchCriteria(province=HCM, building_type="nha_pho",
                                                  budget_vnd=2_200_000_000, estimate_session_id=es.id))
    assert res.criteria.converted_area_m2 == 490 and res.criteria.floors == 4
    kp = next(r for r in res.results if r.contractor.slug == "kien-phong")
    assert kp.estimated_cost.display == "2,107–2,303 tỷ" and kp.estimated_cost.status == "on_dezon"
    assert next(b for b in kp.breakdown if b.key == "budget_fit").score == 1.0
    sc = next(r for r in res.results if r.contractor.slug == "smartcons-tech")
    assert sc.estimated_cost.display == NO_DATA
    log = db.get(RecommendationLog, res.recommendation_log_id)
    assert log.estimate_session_id == es.id


def test_record_click(db):
    res = svc.match_contractors(db, MatchCriteria(province=HCM, building_type="nha_pho"))
    cid = res.results[0].contractor.id
    assert svc.record_click(db, res.recommendation_log_id, cid)
    assert svc.record_click(db, res.recommendation_log_id, cid)
    assert db.get(RecommendationLog, res.recommendation_log_id).clicked_contractor_ids == [cid]
    assert not svc.record_click(db, res.recommendation_log_id, 999_999)
    assert not svc.record_click(db, 999_999, cid)


def test_compare_no_empty_cells(db):
    ids = [_id(db, s) for s in ("kien-phong", "smartcons-tech", "huy-hoang")]
    m = svc.compare_contractors(db, ids)
    assert [c.id for c in m.contractors] == ids
    assert [r.label_vi for r in m.rows] == ["Khu vực", "Công trình trên Dezon", "Kinh nghiệm", "Đơn giá thô",
                                           "Đơn giá trọn gói (hoàn thiện cơ bản)", "Hồ sơ dự án", "Bảo hành", "Mức phù hợp"]
    for row in m.rows:
        assert len(row.cells) == 3
        for cell in row.cells:
            assert cell.display and cell.display.strip()
            if cell.status == "unavailable":
                assert cell.display == NO_DATA and cell.value is None
    rows = {r.key: r for r in m.rows}
    assert rows["rough_price"].cells[0].display == "4,3–4,7 triệu/m²"
    assert rows["rough_price"].cells[1].display == NO_DATA
    assert rows["experience"].cells[2].display == NO_DATA
    assert rows["dezon_project_count"].cells[1].display == "8 công trình ghi nhận trên Dezon"
    assert rows["projects"].cells[0].value["projects"] == 3
    assert all(c.display == NO_DATA for c in rows["fit"].cells)  # không có tiêu chí -> chưa đánh giá


def test_compare_with_criteria_and_order(db):
    ids = [_id(db, s) for s in ("huy-hoang", "kien-phong")]
    m = svc.compare_contractors(db, ids, MatchCriteria(province=HCM, building_type="nha_pho"))
    assert [c.slug for c in m.contractors] == ["huy-hoang", "kien-phong"]
    fit = next(r for r in m.rows if r.key == "fit")
    assert fit.cells[0].display.startswith(FIT_REVIEW) and fit.cells[0].display.endswith("/100")
    assert fit.cells[1].status == "reference"
    assert "Dezon Matching v1" in fit.cells[1].source.label


@pytest.mark.parametrize("bad", [[], [1], [1, 2, 3, 4, 5, 6]])
def test_compare_requires_2_to_5(db, bad):
    with pytest.raises(ValueError):
        svc.compare_contractors(db, bad)


def test_compare_rejects_duplicates_and_unknown(db):
    kp = _id(db, "kien-phong")
    with pytest.raises(ValueError):
        svc.compare_contractors(db, [kp, kp])
    with pytest.raises(ValueError):
        svc.compare_contractors(db, [kp, 999_999])


def test_profile_kien_phong(db):
    p = svc.get_contractor_profile(db, "kien-phong")
    assert p is not None and p.card.name == "Kiến Phong"
    names = {x.name for x in p.projects}
    assert {"France Indochine 22 Hà Nội", "Green Pool Villa Vĩnh Long", "Indochine Villa Quận 7 Sài Gòn"} <= names
    for proj in p.projects:
        assert proj.project_url.startswith("https://") and "Kiến Phong" in proj.contractor_names
        assert proj.role == "thi_cong" and proj.images
    assert p.total_images == len(p.images) == 3
    assert p.info["founded_year"].display == "2018"
    assert p.info["staff_workers"].display == "20 thợ" and p.info["staff_office"].display == "12 văn phòng"
    assert p.info["warranty"].display == NO_DATA
    assert p.description.has_value
    assert svc.get_contractor_profile(db, p.card.id).card.slug == "kien-phong"
    assert svc.get_contractor_profile(db, str(p.card.id)).card.slug == "kien-phong"
    assert svc.get_contractor_profile(db, "khong-ton-tai") is None


def test_profile_missing_description(db):
    p = svc.get_contractor_profile(db, "huy-hoang")
    assert p.description.display == NO_DATA and p.projects == [] and p.total_images == 0
    assert p.info["rough_price"].display == NO_DATA


def test_get_contractor_projects(db):
    kp = _id(db, "kien-phong")
    assert len(svc.get_contractor_projects(db, kp)) == 3
    assert len(svc.get_contractor_projects(db, kp, building_type="biet_thu", limit=2)) == 2
    assert svc.get_contractor_projects(db, kp, building_type="nha_pho") == []
    assert svc.get_contractor_projects(db, 999_999) == []


def test_search_contractors_simple(db):
    cards, total = svc.search_contractors_simple(db, province=HCM)
    assert total == 3 and len(cards) == 3
    assert cards[0].name == "Smartcons Tech"  # sắp theo số công trình ghi nhận giảm dần
    cards, total = svc.search_contractors_simple(db, q="Kiến")
    assert total == 1 and cards[0].slug == "kien-phong"
    cards, total = svc.search_contractors_simple(db, building_type="biet_thu")
    assert {c.slug for c in cards} == {"kien-phong", "smartcons-tech"}
    _, total = svc.search_contractors_simple(db, province="Đà Nẵng")
    assert total == 0
    cards, total = svc.search_contractors_simple(db, province=HCM, limit=1)
    assert total == 3 and len(cards) == 1


def test_contractor_overview_villa_hcm(db):
    ov = svc.contractor_overview(db, HCM, "biet_thu")
    assert ov.contractor_count.value == 2 and ov.contractor_count.display == "2 nhà thầu"
    assert ov.contractors[0].card.slug == "kien-phong" and ov.contractors[0].has_projects_of_type
    assert all(p.project_url for p in ov.contractors[0].projects_of_type)
    assert ov.contractors_with_projects.value == 1
    assert ov.total_dezon_projects.display.startswith("15 công trình ghi nhận trên Dezon")
    assert ov.rough_price_range.display == "4,3–4,7 triệu/m²"
    assert ov.with_warranty_info.display == "0/2 nhà thầu có thông tin bảo hành"
    assert ov.verified_profiles.display == "0/2 hồ sơ đã xác minh"
    blob = ov.model_dump_json()
    assert "tốt nhất" not in blob.lower()


def test_contractor_overview_empty(db):
    ov = svc.contractor_overview(db, "Đà Nẵng", "khach_san")
    assert ov.contractors == [] and ov.contractor_count.display == NO_DATA and ov.notes


# --- Bổ sung sau review ------------------------------------------------------------------------
def test_match_with_unaccented_province(db):
    res = svc.match_contractors(db, MatchCriteria(province="Ho Chi Minh", building_type="nha_pho",
                                                  work_scope="xay_tho"))
    assert len(res.results) == 3 and not res.relaxed_to_neighbors


def test_match_unknown_estimate_and_chat_session_are_safe(db):
    import uuid

    res = svc.match_contractors(db, MatchCriteria(province=HCM, building_type="nha_pho",
                                                  estimate_session_id=uuid.uuid4()),
                                chat_session_id=uuid.uuid4())
    assert any("khái toán" in n for n in res.notes)
    log = db.get(RecommendationLog, res.recommendation_log_id)
    assert log.estimate_session_id is None and log.chat_session_id is None
    res2 = svc.match_contractors(db, MatchCriteria(province=HCM, building_type="nha_pho"),
                                 chat_session_id="khong-phai-uuid")
    assert db.get(RecommendationLog, res2.recommendation_log_id).chat_session_id is None


def test_search_unaccented_name(db):
    for q in ("kien phong", "KIEN PHONG", "Kiến Phong", "huy hoang"):
        cards, total = svc.search_contractors_simple(db, q=q)
        assert total == 1, q
    assert svc.search_contractors_simple(db, q="kien phong")[0][0].slug == "kien-phong"
    assert svc.search_contractors_simple(db, q="100%")[1] == 0  # ký tự LIKE được escape


def test_contractor_overview_type_specific_project_count(db):
    ov = svc.contractor_overview(db, "Ho Chi Minh", "biet_thu")
    assert ov.contractor_count.value == 2
    assert ov.projects_of_type_count.value == 3  # 3 dự án biệt thự của Kiến Phong có trang trên Dezon
    assert ov.projects_of_type_count.display == "3 công trình biệt thự có trang dự án trên Dezon"
    assert ov.projects_of_type_count.status == "on_dezon"
    assert "mọi loại công trình" in ov.total_dezon_projects.display
    empty = svc.contractor_overview(db, "Đà Nẵng", "khach_san")
    assert empty.projects_of_type_count.display == NO_DATA


def test_search_invalid_building_type_raises_value_error(db):
    with pytest.raises(ValueError):
        svc.search_contractors_simple(db, building_type="nha-pho-xyz")
    # Session vẫn dùng được (không gửi giá trị ENUM lạ xuống Postgres)
    assert svc.search_contractors_simple(db, building_type="nha_pho")[1] == 3
