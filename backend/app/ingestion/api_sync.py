"""Đồng bộ từ API/DB chính thức của Dezon (nguồn ưu tiên cao nhất — đặc tả 2.10, 11.2 bước 1).

Trạng thái: SKELETON. Dezon chưa cung cấp tài liệu API (xem docs/DECISIONS.md "Cần hỏi lại Dezon").
Ánh xạ trường dưới đây là GIẢ ĐỊNH và được ghi ở docs/DATA_MAPPING.md; khi có schema thật chỉ cần sửa
`map_contractor` / `map_project` — phần ghi DB dùng chung `upsert.ingest_page` (source_type `dezon_api`).

Khi chưa cấu hình `DEZON_API_BASE_URL` / `DEZON_API_TOKEN` -> trả {"status": "skipped", "reason": ...}.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.evidence import SourceType
from app.ingestion.crawler.fetcher import payload_hash
from app.ingestion.parse_vi import (
    normalize_province,
    parse_building_types,
    parse_money,
    parse_money_range,
)
from app.ingestion.upsert import IngestStats, ingest_page

log = logging.getLogger(__name__)

NOT_CONFIGURED_REASON = ("Chưa cấu hình DEZON_API_BASE_URL/DEZON_API_TOKEN — dùng crawler trang công khai "
                         "cho tới khi Dezon cấp quyền API")


class DezonApiClient:
    """Client REST giả định: GET {base}/contractors?page=N, GET {base}/projects?page=N.

    Phản hồi giả định dạng {"data": [...], "next_page": N|null} hoặc {"items": [...], "next": url|null}.
    """

    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._own = client is None
        self.client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._own:
            self.client.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self.client.get(f"{self.base_url}{path}", params=params,
                               headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json",
                                        "User-Agent": "DezonAIBot/0.1 (api-sync)"})
        resp.raise_for_status()
        return resp.json()

    def iter_resource(self, path: str, max_pages: int = 100) -> Iterator[dict[str, Any]]:
        page = 1
        for _ in range(max_pages):
            body = self._get(path, {"page": page})
            items = body.get("data") or body.get("items") or body.get("results") or []
            yield from items
            nxt = body.get("next_page") or body.get("next")
            if not nxt or not items:
                break
            page = nxt if isinstance(nxt, int) else page + 1

    def resource_url(self, path: str, ident: Any) -> str:
        return f"{self.base_url}{path}/{ident}"


# ------------------------------------------------------------------ ánh xạ API -> dict trung gian
def _range(obj: dict[str, Any], lo_key: str, hi_key: str, text_key: str) -> tuple[int | None, int | None]:
    lo, hi = obj.get(lo_key), obj.get(hi_key)
    if lo is not None or hi is not None:
        return (int(lo) if lo is not None else None, int(hi) if hi is not None else None)
    rng = parse_money_range(obj.get(text_key)) if obj.get(text_key) else None
    return (rng[0], rng[1]) if rng else (None, None)


def map_contractor(obj: dict[str, Any], site_url: str) -> dict[str, Any]:
    """Bản ghi nhà thầu từ API (giả định) -> dict trung gian dùng chung với crawler."""
    slug = obj.get("slug") or str(obj.get("id"))
    rough = _range(obj, "rough_price_min", "rough_price_max", "rough_price_text")
    finish = _range(obj, "finish_price_min", "finish_price_max", "finish_price_text")
    staff = obj.get("staff") or {}
    types = obj.get("building_types") or obj.get("project_types") or []
    areas = obj.get("service_areas") or obj.get("provinces") or []
    province = normalize_province(obj.get("province") or obj.get("city"))
    warranty = obj.get("warranty") or obj.get("warranty_text")
    rec: dict[str, Any] = {
        "kind": "contractor", "slug": slug, "external_id": str(obj.get("id") or slug),
        "name": obj.get("name"), "badge": obj.get("badge") or obj.get("tier_label"), "province": province,
        "founded_year": obj.get("founded_year"), "years_experience": obj.get("years_experience"),
        "staff_workers": staff.get("workers", obj.get("staff_workers")),
        "staff_office": staff.get("office", obj.get("staff_office")),
        "rough_price_min": rough[0], "rough_price_max": rough[1],
        "finish_price_min": finish[0], "finish_price_max": finish[1],
        "typical_project_value": obj.get("typical_project_value")
        if obj.get("typical_project_value") is not None else parse_money(obj.get("typical_project_value_text")),
        "dezon_project_count": obj.get("project_count"),
        "has_warranty_info": bool(warranty) if warranty is not None else None,
        "warranty_text": warranty or None, "description": obj.get("description"),
        "avatar_url": obj.get("avatar_url") or obj.get("logo"),
        "profile_url": obj.get("profile_url") or f"{site_url.rstrip('/')}/nha-thau/{slug}/",
        "building_types": parse_building_types(types if isinstance(types, list) else str(types)),
        "service_provinces": [p for p in (normalize_province(a) for a in areas) if p] or
        ([province] if province else []),
        "source_updated_at": _dt(obj.get("updated_at")),
    }
    if obj.get("is_verified") is True:
        rec["extra"] = {"api_verified": True}  # trạng thái verified do admin duyệt, không tự nâng
    return rec


def map_project(obj: dict[str, Any], site_url: str) -> dict[str, Any]:
    slug = obj.get("slug") or str(obj.get("id"))
    bts = parse_building_types([obj["building_type"]]) if obj.get("building_type") else []
    return {
        "kind": "project", "slug": slug, "external_id": f"api:{obj.get('id') or slug}", "name": obj.get("name"),
        "building_type": bts[0] if bts else None, "province": normalize_province(obj.get("province")),
        "district": obj.get("district"), "floors": obj.get("floors"), "land_area_m2": obj.get("land_area_m2"),
        "floor_area_m2": obj.get("floor_area_m2"), "start_date": obj.get("start_date"),
        "end_date": obj.get("end_date"), "status": obj.get("status"), "description": obj.get("description"),
        "project_url": obj.get("url") or f"{site_url.rstrip('/')}/du-an/{slug}/",
        "images": [{"url": u if isinstance(u, str) else u.get("url"),
                    "caption": None if isinstance(u, str) else u.get("caption"), "sort_order": i}
                   for i, u in enumerate(obj.get("images") or [])],
        "contractors": [{"slug": c.get("slug"), "external_id": str(c.get("id")) if c.get("id") else None,
                         "name": c.get("name"), "role": c.get("role") or "thi_cong",
                         "profile_url": c.get("profile_url")}
                        for c in (obj.get("contractors") or [])],
        "source_updated_at": _dt(obj.get("updated_at")),
    }


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------ chạy đồng bộ
def is_configured() -> bool:
    s = get_settings()
    return bool(s.dezon_api_base_url and s.dezon_api_token)


def run_api_sync(db: Session, client: DezonApiClient | None = None, max_pages: int = 100,
                 commit: bool = True) -> dict[str, Any]:
    """Đồng bộ nhà thầu rồi dự án từ API. Chưa cấu hình -> {"status": "skipped", "reason": ...}."""
    settings = get_settings()
    if client is None:
        if not is_configured():
            return {"status": "skipped", "reason": NOT_CONFIGURED_REASON}
        client = DezonApiClient(settings.dezon_api_base_url, settings.dezon_api_token)
        own = True
    else:
        own = False
    stats = IngestStats()
    try:
        for path, mapper, label_prefix in (("/contractors", map_contractor, "Dữ liệu Dezon (API) — nhà thầu"),
                                           ("/projects", map_project, "Dữ liệu Dezon (API) — dự án")):
            for obj in client.iter_resource(path, max_pages=max_pages):
                stats.pages += 1
                if not isinstance(obj, dict) or not (obj.get("slug") or obj.get("id") is not None):
                    stats.skipped += 1  # không có định danh -> không tạo bản ghi slug "None"
                    continue
                try:
                    rec = mapper(obj, settings.dezon_site_url)
                except (TypeError, ValueError, KeyError, AttributeError) as exc:
                    log.warning("Bỏ qua bản ghi API %s%s không ánh xạ được: %s", path, obj.get("id"), exc)
                    stats.errors += 1
                    continue
                outcomes = ingest_page(db, [rec], url=client.resource_url(path, obj.get("id") or rec["slug"]),
                                       label=f"{label_prefix} {rec.get('name') or rec['slug']}",
                                       source_type=SourceType.dezon_api, raw_hash=payload_hash(obj),
                                       source_updated_at=rec.get("source_updated_at"),
                                       create_missing_contractors=True)
                for out in outcomes:
                    stats.add(out)
            if commit:
                db.commit()
    except httpx.HTTPError as exc:
        log.warning("Lỗi gọi Dezon API: %s", exc)
        result = {"status": "error", "reason": f"Lỗi gọi Dezon API: {exc}", **stats.as_dict()}
        result["changed_entities"] = stats.changed_entities
        return result
    finally:
        if own:
            client.close()
    result = {"status": "ok", **stats.as_dict()}
    result["changed_entities"] = stats.changed_entities
    return result
