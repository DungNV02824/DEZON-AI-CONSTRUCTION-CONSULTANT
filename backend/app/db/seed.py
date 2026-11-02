"""Seed dữ liệu dev (đặc tả mục 6.2, 7.1, 11.3). Chạy lặp lại an toàn (idempotent).

    python -m app.db.seed

- Hệ số khái toán mục 6.2: approved=false, nguồn dezon_editorial (chờ Dezon duyệt).
- Đơn giá mẫu: trạng thái `reference` — KHÔNG phải bảng giá chính thức của Dezon.
- Fixture nhà thầu: Kiến Phong (dữ liệu quan sát 31/10/2026) + 2 hồ sơ thiếu dữ liệu.
"""
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ConstructionRate,
    Contractor,
    ContractorCapability,
    DataSource,
    EstimateRule,
    MatchingWeights,
    Project,
    ProjectContractor,
    ProjectImage,
    ProvinceNeighbor,
)
from app.db.session import SessionLocal

HCM = "TP. Hồ Chí Minh"
HN = "Hà Nội"
OBSERVED = datetime(2026, 10, 31, tzinfo=UTC)

# --- 6.2 Hệ số mặc định ------------------------------------------------------------------------
RULES: list[tuple[str, str, str, float, str]] = [
    ("floor_standard", "tang", "Tầng trệt / lầu (có mái che)", 1.00, "floor_area"),
    ("mezzanine", "tang", "Tầng lửng", 1.00, "mezzanine_area"),
    ("foundation_single", "mong", "Móng đơn", 0.30, "ground_floor_area"),
    ("foundation_strip", "mong", "Móng băng", 0.50, "ground_floor_area"),
    ("foundation_pile", "mong", "Móng cọc (chưa gồm ép cọc)", 0.40, "ground_floor_area"),
    ("basement_lt_1_3", "ham", "Hầm sâu < 1,3 m", 1.50, "basement_area"),
    ("basement_1_3_1_7", "ham", "Hầm sâu 1,3–1,7 m", 1.70, "basement_area"),
    ("basement_1_7_2_0", "ham", "Hầm sâu 1,7–2,0 m", 2.00, "basement_area"),
    ("basement_gt_2_0", "ham", "Hầm sâu > 2,0 m", 2.50, "basement_area"),
    ("roof_metal", "mai", "Mái tôn", 0.30, "roof_area"),
    ("roof_concrete", "mai", "Mái bê tông cốt thép", 0.50, "roof_area"),
    ("roof_tile_on_truss", "mai", "Mái ngói kèo thép", 0.70, "roof_area"),
    ("roof_tile_on_concrete", "mai", "Mái ngói đổ BTCT", 1.00, "roof_area"),
    ("rooftop_covered", "san_thuong", "Sân thượng có mái che", 0.70, "rooftop_area"),
    ("rooftop_open", "san_thuong", "Sân thượng không mái", 0.50, "rooftop_area"),
    ("yard", "san", "Sân trước/sau", 0.50, "yard_area"),
]
DEFAULT_RULE_CODES = {"floor_standard", "foundation_strip", "foundation_pile", "roof_concrete"}

# --- Đơn giá mẫu (VND/m², mức trung bình) — dữ liệu tham khảo cho dev ---------------------------
BASE_RATES: dict[tuple[str, str], tuple[int, int]] = {
    # (building_type, work_scope): (min, max) toàn quốc
    ("nha_pho", "xay_tho"): (3_400_000, 4_000_000),
    ("nha_pho", "hoan_thien"): (2_500_000, 3_500_000),
    ("nha_pho", "tron_goi"): (5_500_000, 6_800_000),
    ("nha_pho", "noi_that"): (2_000_000, 4_000_000),
    ("biet_thu", "xay_tho"): (4_000_000, 4_800_000),
    ("biet_thu", "hoan_thien"): (3_200_000, 4_500_000),
    ("biet_thu", "tron_goi"): (6_800_000, 8_500_000),
    ("biet_thu", "noi_that"): (2_800_000, 5_500_000),
    ("can_ho", "hoan_thien"): (2_500_000, 3_800_000),
    ("can_ho", "noi_that"): (2_500_000, 5_000_000),
    ("van_phong", "xay_tho"): (3_600_000, 4_300_000),
    ("van_phong", "tron_goi"): (6_000_000, 7_500_000),
    ("khach_san", "xay_tho"): (4_200_000, 5_000_000),
    ("khach_san", "tron_goi"): (7_500_000, 9_500_000),
}
TIER_FACTOR = {"tiet_kiem": 0.9, "trung_binh": 1.0, "kha": 1.15, "cao_cap": 1.35}
PROVINCE_FACTOR = {"*": 1.0, HCM: 1.05, HN: 1.05}

WEIGHTS_V1 = {"location": 0.20, "building_type": 0.20, "budget_fit": 0.20, "experience": 0.15,
              "similar_projects": 0.15, "profile_quality": 0.10}

NEIGHBORS: dict[str, list[str]] = {
    HCM: ["Bình Dương", "Đồng Nai", "Long An", "Bà Rịa - Vũng Tàu", "Tây Ninh"],
    HN: ["Bắc Ninh", "Hưng Yên", "Hà Nam", "Vĩnh Phúc", "Hòa Bình", "Thái Nguyên"],
    "Đà Nẵng": ["Quảng Nam", "Thừa Thiên Huế"],
    "Vĩnh Long": ["Cần Thơ", "Tiền Giang", "Bến Tre", "Trà Vinh", "Đồng Tháp"],
}


def _round50k(x: float) -> int:
    return int(round(x / 50_000) * 50_000)


def get_or_create_source(db: Session, *, url: str | None, label: str, source_type: str, status: str,
                         source_updated_at: datetime | None = None) -> DataSource:
    if url:
        ds = db.scalar(select(DataSource).where(DataSource.url == url))
        if ds:
            return ds
    else:
        ds = db.scalar(select(DataSource).where(DataSource.label == label, DataSource.url.is_(None)))
        if ds:
            return ds
    ds = DataSource(url=url, label=label, source_type=source_type, status=status,
                    source_updated_at=source_updated_at)
    db.add(ds)
    db.flush()
    return ds


def seed_rules(db: Session) -> None:
    src = get_or_create_source(db, url=None, label="Hệ số khái toán mặc định (seed dev — chờ Dezon duyệt)",
                               source_type="dezon_editorial", status="reference")
    for code, group, label, k, base in RULES:
        if db.scalar(select(EstimateRule).where(EstimateRule.code == code, EstimateRule.version == 1)):
            continue
        db.add(EstimateRule(code=code, group_name=group, label_vi=label, coefficient=k, base=base,
                            is_default=code in DEFAULT_RULE_CODES, version=1, approved=False,
                            source_id=src.id))


def seed_rates(db: Session) -> None:
    src = get_or_create_source(db, url=None, label="Đơn giá tham khảo (seed dev — chờ Dezon cung cấp bảng giá)",
                               source_type="dezon_editorial", status="reference",
                               source_updated_at=OBSERVED)
    eff = date(2026, 1, 1)
    for (btype, scope), (pmin, pmax) in BASE_RATES.items():
        for province, pf in PROVINCE_FACTOR.items():
            for tier, tf in TIER_FACTOR.items():
                exists = db.scalar(select(ConstructionRate.id).where(
                    ConstructionRate.building_type == btype, ConstructionRate.province == province,
                    ConstructionRate.work_scope == scope, ConstructionRate.quality_tier == tier,
                    ConstructionRate.effective_from == eff))
                if exists:
                    continue
                db.add(ConstructionRate(building_type=btype, province=province, work_scope=scope,
                                        quality_tier=tier, price_min=_round50k(pmin * pf * tf),
                                        price_max=_round50k(pmax * pf * tf), effective_from=eff,
                                        source_id=src.id))


def seed_weights(db: Session) -> None:
    if not db.get(MatchingWeights, 1):
        db.add(MatchingWeights(version=1, weights=WEIGHTS_V1, is_active=True,
                               note="Trọng số khởi đầu (mục 7.1) — thử nghiệm"))


def seed_neighbors(db: Session) -> None:
    for prov, neighbors in NEIGHBORS.items():
        for n in neighbors:
            for a, b in ((prov, n), (n, prov)):
                if not db.get(ProvinceNeighbor, (a, b)):
                    db.add(ProvinceNeighbor(province=a, neighbor=b))
                    db.flush()


CONTRACTORS = [
    {
        "slug": "kien-phong", "name": "Kiến Phong", "badge": "Nhà thầu tiềm năng", "province": HCM,
        "founded_year": 2018, "years_experience": 8, "staff_workers": 20, "staff_office": 12,
        "rough_price_min": 4_300_000, "rough_price_max": 4_700_000,
        "finish_price_min": 7_300_000, "finish_price_max": 9_500_000,
        "typical_project_value": 25_000_000_000, "dezon_project_count": 7,
        "has_warranty_info": None, "warranty_text": None,
        "description": ("Kiến Phong là đơn vị thiết kế và thi công nhà phố, biệt thự, căn hộ tại TP. Hồ Chí Minh, "
                        "theo phong cách Indochine và hiện đại."),
        "capabilities": [("nha_pho", HCM), ("biet_thu", HCM), ("can_ho", HCM)],
        "projects": [
            ("france-indochine-22-ha-noi", "France Indochine 22 Hà Nội", "biet_thu", HN, None),
            ("green-pool-villa-vinh-long", "Green Pool Villa Vĩnh Long", "biet_thu", "Vĩnh Long", None),
            ("indochine-villa-quan-7-sai-gon", "Indochine Villa Quận 7 Sài Gòn", "biet_thu", HCM, "Quận 7"),
        ],
    },
    {
        "slug": "smartcons-tech", "name": "Smartcons Tech", "badge": None, "province": HCM,
        "dezon_project_count": 8,
        "description": "Đơn vị thi công xây dựng nhà ở tại TP. Hồ Chí Minh.",
        "capabilities": [("nha_pho", HCM), ("biet_thu", HCM)],
        "projects": [],
    },
    {
        "slug": "huy-hoang", "name": "Huy Hoàng", "badge": None, "province": HCM,
        "dezon_project_count": 6,
        "description": None,
        "capabilities": [("nha_pho", HCM)],
        "projects": [],
    },
]


def seed_contractors(db: Session, site: str = "https://dezon.vn") -> None:
    for c in CONTRACTORS:
        if db.scalar(select(Contractor.id).where(Contractor.slug == c["slug"])):
            continue
        profile_url = f"{site}/nha-thau/{c['slug']}/"  # URL thật có '/' cuối (docs/DATA_MAPPING.md)
        src = get_or_create_source(db, url=profile_url, label=f"Hồ sơ nhà thầu {c['name']}",
                                   source_type="crawl_public", status="on_dezon", source_updated_at=OBSERVED)
        fields = {k: v for k, v in c.items() if k not in ("capabilities", "projects")}
        contractor = Contractor(**fields, profile_url=profile_url, source_id=src.id,
                                avatar_url=f"https://placehold.co/160x160?text={c['name'].replace(' ', '+')}",
                                field_sources={k: src.id for k, v in fields.items() if v is not None})
        db.add(contractor)
        db.flush()
        for btype, prov in c["capabilities"]:
            db.add(ContractorCapability(contractor_id=contractor.id, building_type=btype, province=prov,
                                        source_id=src.id))
        for i, (pslug, pname, ptype, pprov, pdist) in enumerate(c["projects"]):
            # Công trình thực tế nằm dưới hồ sơ nhà thầu: /nha-thau/<slug>/<công-trình>/ (giống crawler)
            purl = f"{profile_url}{pslug}/"
            psrc = get_or_create_source(db, url=purl, label=f"Dự án {pname}", source_type="crawl_public",
                                        status="on_dezon", source_updated_at=OBSERVED)
            project = Project(slug=f"{c['slug']}--{pslug}", external_id=f"nha-thau/{c['slug']}/{pslug}",
                              name=pname, building_type=ptype, province=pprov, district=pdist,
                              project_url=purl, source_id=psrc.id)
            db.add(project)
            db.flush()
            db.add(ProjectContractor(project_id=project.id, contractor_id=contractor.id, role="thi_cong"))
            db.add(ProjectImage(project_id=project.id, sort_order=0, caption=f"{pname} (ảnh minh họa dev)",
                                url=f"https://placehold.co/800x600?text=Project+{i + 1}"))


def run(db: Session) -> None:
    seed_rules(db)
    seed_rates(db)
    seed_weights(db)
    seed_neighbors(db)
    seed_contractors(db)
    db.flush()
    # Mở rộng: các module khác đăng ký hook seed bổ sung ở đây (profile_completeness, RAG, snapshot).
    for hook in EXTRA_SEED_HOOKS:
        hook(db)
    db.commit()


# Danh sách callable(db) chạy sau seed cơ bản — module ingestion/rag/matching có thể append.
EXTRA_SEED_HOOKS: list = []


def main() -> None:
    try:
        import app.db.seed_hooks  # noqa: F401  (đăng ký hook nếu có)
    except ImportError:
        pass
    with SessionLocal() as db:
        run(db)
    print("Seed OK")


if __name__ == "__main__":
    # `python -m app.db.seed` chạy file này dưới tên `__main__`, còn seed_hooks đăng ký hook vào module
    # `app.db.seed` -> phải gọi main() của module `app.db.seed` để run() đọc đúng danh sách EXTRA_SEED_HOOKS.
    from app.db import seed as _seed_module

    _seed_module.main()
