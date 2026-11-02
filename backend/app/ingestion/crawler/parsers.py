"""Parse HTML công khai của dezon.vn thành dict trung gian (xem docs/DATA_MAPPING.md).

Cấu trúc được khảo sát trên trang thật ngày 02/11/2026 (fixture: tests/fixtures/dezon/):
- Danh sách nhà thầu `/nha-thau/` và `/nha-thau/<nhóm>/` (vd `/nha-thau/tiem-nang/`):
  thẻ `article[data-card]` (tên, khu vực, số công trình, loại công trình) và `a.dzct-card`
  (nhà thầu thương hiệu: tên, số công trình, ảnh bìa, data-cats).
- Hồ sơ nhà thầu `/nha-thau/<slug>/`: `h1`, breadcrumb nhóm (badge), `section#gioi-thieu`,
  bảng `div.dznt-spec > div.dznt-sr` (nhãn `span.dznt-sl` / giá trị `div.dznt-sv`),
  `section#cong-trinh a` (công trình thực tế, URL `/nha-thau/<slug>/<công-trình>/`).
- Trang công trình của nhà thầu `/nha-thau/<slug>/<công-trình>/`: `h1`, dòng mô tả, ảnh `a.dznt-shot`.
- Danh sách dự án `/du-an/` (phân trang `/du-an/page/N/`): `div.item_post`.
- Trang dự án `/du-an/<slug>/`: `h1`, breadcrumb loại, bảng `table.jtable`, ảnh `.project_slider`,
  giới thiệu `#dz-sec-intro`.

Dict trung gian KHÔNG chứa giá trị suy diễn: trường không có trên trang -> None.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from app.ingestion.parse_vi import (
    clean_text,
    fold_vi,
    infer_building_type_from_name,
    is_placeholder,
    map_building_type,
    normalize_province,
    parse_area_m2,
    parse_building_types,
    parse_count,
    parse_date_vi,
    parse_money,
    parse_money_range,
    parse_staff,
    parse_year,
    parse_years,
    split_list,
)

SITE = "https://dezon.vn"
# Nhóm trong /nha-thau/<nhóm>/ — không phải slug nhà thầu.
CONTRACTOR_GROUP_SLUGS = {"tiem-nang", "go", "mep", "thi-cong", "page"}


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def _text(el: Tag | None) -> str:
    return clean_text(el.get_text(" ", strip=True)) if el is not None else ""


def _abs(url: str | None, base: str = SITE) -> str | None:
    if not url or url.startswith("data:"):
        return None
    return urljoin(base + "/", url)


def _path_parts(url: str) -> list[str]:
    return [p for p in urlsplit(url).path.split("/") if p]


def contractor_slug_from_url(url: str) -> str | None:
    """https://dezon.vn/nha-thau/kien-phong/ -> 'kien-phong' (bỏ qua trang nhóm)."""
    parts = _path_parts(url)
    if len(parts) >= 2 and parts[0] == "nha-thau" and parts[1] not in CONTRACTOR_GROUP_SLUGS:
        return parts[1]
    return None


def canonical_url(soup: BeautifulSoup, fallback: str) -> str:
    for sel, attr in (('link[rel="canonical"]', "href"), ('meta[property="og:url"]', "content")):
        el = soup.select_one(sel)
        if el and el.get(attr):
            return str(el.get(attr))
    return fallback


def page_modified_at(soup: BeautifulSoup) -> datetime | None:
    """`article:modified_time` (Yoast) -> ngày cập nhật thực tế trên nguồn."""
    el = soup.select_one('meta[property="article:modified_time"]')
    if el and el.get("content"):
        try:
            return datetime.fromisoformat(str(el["content"]).replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _img_src(img: Tag | None) -> str | None:
    if img is None:
        return None
    for attr in ("data-src", "src", "data-bg"):
        v = img.get(attr)
        if v and not str(v).startswith("data:"):
            return _abs(str(v))
    return None


# ================================================================== danh sách nhà thầu
def _list_page_badge(soup: BeautifulSoup, page_url: str) -> str | None:
    """Trang nhóm `/nha-thau/tiem-nang/` -> 'Nhà thầu tiềm năng' (h1)."""
    parts = _path_parts(canonical_url(soup, page_url))
    if len(parts) == 2 and parts[0] == "nha-thau" and parts[1] in CONTRACTOR_GROUP_SLUGS:
        h1 = _text(soup.select_one("h1"))
        return h1 or None
    return None


def _section_badge(card: Tag) -> str | None:
    """Thẻ nằm dưới khối `#tiem-nang` của trang /nha-thau/ -> nhãn khối đó."""
    section = card.find_parent("section")
    header = section.find_previous_sibling("div") if section else None
    if header is not None and header.get("id"):
        h2 = header.select_one("h2")
        return _text(h2) or None
    return None


def _parse_article_card(card: Tag, badge: str | None) -> dict[str, Any] | None:
    name_a = card.select_one("a.dznt-name") or card.select_one("h3 a")
    if name_a is None:
        return None
    url = _abs(name_a.get("href"))
    slug = contractor_slug_from_url(url or "")
    if not slug:
        return None
    rec: dict[str, Any] = {"kind": "contractor", "slug": slug, "external_id": slug, "name": _text(name_a),
                           "profile_url": url}
    info_p = name_a.find_parent("h3").find_next_sibling("p") if name_a.find_parent("h3") else None
    loc_p = card.find("p")
    if info_p is not None and "công trình" in _text(info_p):
        # Bố cục trang nhóm: "TP. HCM · <b>7</b> công trình · <b>8</b> năm"
        parts = [p.strip() for p in _text(info_p).split("·")]
        rec["province"] = normalize_province(parts[0]) if parts else None
        for part in parts[1:]:
            if "công trình" in part:
                rec["dezon_project_count"] = parse_count(part)
            elif "năm" in part:
                years = parse_years(part)
                if years:  # "0 năm" = chưa khai báo
                    rec["years_experience"] = years
        tags = [_text(s) for s in card.select("div > span") if _text(s) and _text(s) != "Đề xuất"]
        if not tags and card.get("data-tags"):
            tags = str(card["data-tags"]).split("|")
        rec["building_types"] = parse_building_types(tags)
    else:
        # Bố cục trang /nha-thau/: <p>khu vực</p><h3>tên</h3><p>loại</p> ... <strong>7</strong> công trình
        if loc_p is not None and loc_p.find_next("h3") is not None:
            rec["province"] = normalize_province(_text(loc_p))
        types_p = name_a.find_parent("h3").find_next_sibling("p") if name_a.find_parent("h3") else None
        rec["building_types"] = parse_building_types(_text(types_p))
        for span in card.select("span"):
            if "công trình" in _text(span):
                rec["dezon_project_count"] = parse_count(_text(span))
                break
    if badge:
        rec["badge"] = badge
    img = card.select_one("img.dz-dc-img") or card.select_one("img")
    cover = _img_src(img)
    if cover:
        rec["cover_url"] = cover
    return rec


def _parse_brand_card(card: Tag) -> dict[str, Any] | None:
    url = _abs(card.get("href"))
    slug = contractor_slug_from_url(url or "")
    if not slug:
        return None
    rec: dict[str, Any] = {"kind": "contractor", "slug": slug, "external_id": slug,
                           "name": _text(card.select_one(".dzct-name")) or clean_text(card.get("title")),
                           "profile_url": url,
                           "dezon_project_count": parse_count(_text(card.select_one(".dzct-cnt")))}
    cover = card.select_one(".dzct-cover")
    if cover is not None and cover.get("data-bg"):
        rec["cover_url"] = _abs(str(cover["data-bg"]))
    cats = [c for c in str(card.get("data-cats") or "").split() if c]
    if cats:
        rec["building_types"] = parse_building_types([c.replace("-", " ") for c in cats])
    return rec


def parse_contractor_list(html: str, page_url: str = f"{SITE}/nha-thau/") -> list[dict[str, Any]]:
    """Trang danh sách nhà thầu -> list dict (chỉ gồm trường hiển thị trên thẻ)."""
    soup = _soup(html)
    page_badge = _list_page_badge(soup, page_url)
    out: dict[str, dict[str, Any]] = {}
    for card in soup.select("article[data-card]"):
        rec = _parse_article_card(card, page_badge or _section_badge(card))
        if rec and rec["slug"] not in out:
            out[rec["slug"]] = rec
    for card in soup.select("a.dzct-card"):
        rec = _parse_brand_card(card)
        if rec and rec["slug"] not in out:
            out[rec["slug"]] = rec
    return list(out.values())


# ================================================================== hồ sơ nhà thầu
SPEC_LABELS = {
    "nam thanh lap": "founded_year",
    "don gia tho": "rough_price",
    "don gia xay tho": "rough_price",
    "don gia hoan thien co ban": "finish_price",
    "don gia hoan thien": "finish_price",
    "so luong nhan su": "staff",
    "nhan su": "staff",
    "loai hinh cong trinh": "building_types",
    "so nam kinh nghiem": "years_experience",
    "kinh nghiem": "years_experience",
    "quy mo du an": "typical_project_value",
    "khu vuc da va dang thi cong": "service_area",
    "khu vuc hoat dong": "service_area",
    "bao hanh": "warranty",
    "chinh sach bao hanh": "warranty",
    "vai tro": "role",
    "van hanh": "operation",
}

CONTRACTOR_PROFILE_FIELDS = [
    "name", "badge", "province", "founded_year", "years_experience", "staff_workers", "staff_office",
    "rough_price_min", "rough_price_max", "finish_price_min", "finish_price_max", "typical_project_value",
    "dezon_project_count", "has_warranty_info", "warranty_text", "description", "avatar_url", "profile_url",
]


def _spec_rows(soup: BeautifulSoup) -> dict[str, str]:
    rows: dict[str, str] = {}
    for row in soup.select(".dznt-spec .dznt-sr"):
        label = _text(row.select_one(".dznt-sl"))
        value = _text(row.select_one(".dznt-sv"))
        if label:
            rows[fold_vi(label).strip(" :")] = value
    return rows


def parse_contractor_profile(html: str, url: str) -> dict[str, Any]:
    """Hồ sơ nhà thầu -> dict đầy đủ các trường của `contractors` (thiếu = None) + capabilities + works."""
    soup = _soup(html)
    profile_url = canonical_url(soup, url)
    slug = contractor_slug_from_url(profile_url) or contractor_slug_from_url(url)
    rec: dict[str, Any] = {f: None for f in CONTRACTOR_PROFILE_FIELDS}
    rec.update({"kind": "contractor", "slug": slug, "external_id": slug, "profile_url": profile_url,
                "building_types": [], "service_provinces": [], "works": [], "extra": {}})
    main = soup.select_one("main") or soup
    rec["name"] = _text(main.select_one("h1")) or None

    # badge: nhóm trong breadcrumb (vd "Nhà thầu tiềm năng")
    for a in main.select("div a[href*='/nha-thau/']"):
        parts = _path_parts(a.get("href", ""))
        if len(parts) == 2 and parts[1] in CONTRACTOR_GROUP_SLUGS:
            rec["badge"] = _text(a) or None
            break

    # Dòng thống kê đầu trang: "<b>7</b> dự án trên Dezon · <b>TP. HCM</b> · Công trình <b>8 năm kinh nghiệm</b>"
    header = main.select_one("section")
    header_types: list[str] = []
    if header is not None:
        sub = header.select_one("h1 + p")
        if sub is not None:
            items = split_list(_text(sub))
            header_types = [i for i in items if not fold_vi(i).startswith("nha thau")]
        for span in header.select("div > span"):
            t = _text(span)
            ft = fold_vi(t)
            if "du an tren dezon" in ft or "cong trinh tren dezon" in ft:
                rec["dezon_project_count"] = parse_count(t)
            elif "kinh nghiem" in ft:
                years = parse_years(t)
                rec["years_experience"] = years if years else None
            elif span.find("strong") is not None and rec["province"] is None:
                rec["province"] = normalize_province(t)

    intro = main.select_one("section#gioi-thieu p")
    if intro is not None and not is_placeholder(_text(intro)):
        rec["description"] = clean_text(intro.get_text("\n", strip=True))

    spec = _spec_rows(soup)
    rec["extra"]["spec"] = spec
    for label, value in spec.items():
        key = SPEC_LABELS.get(label)
        if key is None or is_placeholder(value):
            continue
        if key == "founded_year":
            rec["founded_year"] = parse_year(value)
        elif key in ("rough_price", "finish_price"):
            rng = parse_money_range(value)
            if rng:
                rec[f"{key}_min"], rec[f"{key}_max"] = rng
        elif key == "staff":
            rec["staff_workers"], rec["staff_office"] = parse_staff(value)
        elif key == "building_types":
            header_types = split_list(value)
        elif key == "years_experience":
            years = parse_years(value)
            rec["years_experience"] = years if years else None
        elif key == "typical_project_value":
            rec["typical_project_value"] = parse_money(value)  # khoảng ('7 – 15 tỷ') -> None
            rng = parse_money_range(value)
            if rng and rng[0] != rng[1]:
                rec["extra"]["typical_project_value_range"] = rng
        elif key == "service_area":
            provs = [normalize_province(p) for p in split_list(value)]
            rec["service_provinces"] = [p for p in dict.fromkeys(provs) if p]
        elif key == "warranty":
            rec["has_warranty_info"] = True
            rec["warranty_text"] = value
        else:
            rec["extra"][key] = value
    rec["building_types"] = parse_building_types(header_types)
    if rec["province"] is None and rec["service_provinces"]:
        rec["province"] = rec["service_provinces"][0]
    if not rec["service_provinces"] and rec["province"]:
        rec["service_provinces"] = [rec["province"]]

    og = soup.select_one('meta[property="og:image"]')
    rec["avatar_url"] = _abs(str(og["content"])) if og is not None and og.get("content") else None

    seen: set[str] = set()
    for a in main.select("section#cong-trinh a[href]"):
        wurl = _abs(a.get("href"))
        parts = _path_parts(wurl or "")
        if len(parts) != 3 or parts[0] != "nha-thau" or wurl in seen:
            continue
        seen.add(wurl)
        rec["works"].append({"slug": parts[2], "name": _text(a.select_one("h3")) or None, "url": wurl,
                             "thumb_url": _img_src(a.select_one("img"))})
    rec["source_updated_at"] = page_modified_at(soup)
    return rec


# ================================================================== công trình của nhà thầu
def work_project_slug(contractor_slug: str, work_slug: str) -> str:
    """Slug dự án cho công trình nằm dưới hồ sơ nhà thầu (tránh trùng với /du-an/<slug>/)."""
    return f"{contractor_slug}--{work_slug}"


def parse_contractor_work(html: str, url: str) -> dict[str, Any]:
    """Trang `/nha-thau/<slug>/<công-trình>/` -> dict dự án (role thi_cong)."""
    soup = _soup(html)
    project_url = canonical_url(soup, url)
    parts = _path_parts(project_url)
    contractor_slug = parts[1] if len(parts) >= 3 else contractor_slug_from_url(url)
    work_slug = parts[2] if len(parts) >= 3 else parts[-1]
    main = soup.select_one("main") or soup
    name = _text(main.select_one("h1")) or None
    rec: dict[str, Any] = {
        "kind": "project", "slug": work_project_slug(contractor_slug or "", work_slug),
        "external_id": "/".join(parts), "name": name, "project_url": project_url,
        "building_type": infer_building_type_from_name(name), "province": None, "district": None,
        "floors": None, "land_area_m2": None, "floor_area_m2": None, "start_date": None, "end_date": None,
        "status": None, "description": None, "images": [], "contractors": [], "extra": {},
    }
    meta_p = main.select_one(".dznt-work-head + p") or main.select_one("h1 ~ p")
    if meta_p is not None:
        line = _text(meta_p)
        rec["extra"]["meta_line"] = line
        for seg in [s.strip() for s in line.split("·")]:
            if seg.startswith("Đăng ngày"):
                rec["extra"]["posted_at"] = parse_date_vi(seg)
            elif "ảnh" in seg or "thi công" in seg:
                continue
            elif rec["province"] is None:
                rec["province"] = normalize_province(seg)
        a = meta_p.select_one("a[href*='/nha-thau/']")
        role = "thi_cong" if "thi công" in line else ("thiet_ke" if "thiết kế" in line else "thi_cong")
        if a is not None:
            cslug = contractor_slug_from_url(_abs(a.get("href")) or "")
            if cslug:
                rec["contractors"].append({"slug": cslug, "name": _text(a), "profile_url": _abs(a.get("href")),
                                           "role": role})
    if not rec["contractors"] and contractor_slug:
        rec["contractors"].append({"slug": contractor_slug, "name": None,
                                   "profile_url": f"{SITE}/nha-thau/{contractor_slug}/", "role": "thi_cong"})
    for i, a in enumerate(main.select("a.dznt-shot")):
        src = _abs(a.get("href")) or _img_src(a.select_one("img"))
        if src:
            img = a.select_one("img")
            alt = img.get("alt") if img is not None else None
            rec["images"].append({"url": src, "caption": clean_text(alt) or name, "sort_order": i})
    rec["source_updated_at"] = page_modified_at(soup)
    return rec


# ================================================================== danh sách & trang dự án
def parse_project_list(html: str, page_url: str = f"{SITE}/du-an/") -> dict[str, Any]:
    """Trang `/du-an/` -> {"items": [...], "next_page": url|None}."""
    soup = _soup(html)
    items: dict[str, dict[str, Any]] = {}
    for post in soup.select("div.item_post"):
        title_a = post.select_one(".post_title a") or post.select_one("h4 a")
        if title_a is None:
            continue
        url = _abs(title_a.get("href"))
        parts = _path_parts(url or "")
        if len(parts) != 2 or parts[0] != "du-an" or parts[1] == "page":
            continue
        cats = [_text(a) for a in post.select(".post_cate a")]
        studio = post.select_one("a.post_studio")
        items.setdefault(parts[1], {
            "kind": "project", "slug": parts[1], "external_id": "/".join(parts), "name": _text(title_a),
            "project_url": url, "building_type": next((b for b in parse_building_types(cats)), None),
            "categories": cats, "thumb_url": _img_src(post.select_one("img.lazyload") or post.select_one("img")),
            "studio": re.sub(r"^By\s+", "", _text(studio)) if studio is not None else None,
        })
    return {"items": list(items.values()), "next_page": next_page_url(html, page_url)}


def next_page_url(html: str, page_url: str) -> str | None:
    """Trang kế tiếp dạng WordPress `/page/N/` (N = trang hiện tại + 1) nếu có link."""
    soup = _soup(html) if isinstance(html, str) else html
    m = re.search(r"/page/(\d+)/?$", urlsplit(page_url).path)
    current = int(m.group(1)) if m else 1
    want = current + 1
    for a in soup.select("a[href*='/page/']"):
        href = _abs(a.get("href"), page_url) or ""
        m2 = re.search(r"/page/(\d+)/?$", urlsplit(href).path)
        if m2 and int(m2.group(1)) == want:
            return href
    return None


PROJECT_INFO_LABELS = {
    "du an": "name", "dien tich": "area", "dia diem": "location", "nha thau thi cong": "contractor",
    "nha thau": "contractor", "cong ty": "studio", "nhiep anh gia": "photographer",
    "nam hoan thien": "completed_year", "so tang": "floors", "loai cong trinh": "type",
}


def parse_project_page(html: str, url: str) -> dict[str, Any]:
    """Trang `/du-an/<slug>/` -> dict dự án + ảnh + nhà thầu thi công."""
    soup = _soup(html)
    project_url = canonical_url(soup, url)
    parts = _path_parts(project_url)
    name = _text(soup.select_one("h1")) or None
    rec: dict[str, Any] = {
        "kind": "project", "slug": parts[-1] if parts else None, "external_id": "/".join(parts), "name": name,
        "project_url": project_url, "building_type": None, "province": None, "district": None, "floors": None,
        "land_area_m2": None, "floor_area_m2": None, "start_date": None, "end_date": None, "status": None,
        "description": None, "images": [], "contractors": [], "extra": {},
    }
    crumb = soup.select_one(".breadcrumb_last")
    rec["building_type"] = map_building_type(_text(crumb)) if crumb is not None else None
    if rec["building_type"] == "khac":
        rec["building_type"] = None
    if rec["building_type"] is None:
        rec["building_type"] = infer_building_type_from_name(name)

    for tr in soup.select("table.jtable tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        key = PROJECT_INFO_LABELS.get(fold_vi(_text(tds[0])).strip(" :"))
        cell = tds[1]
        value = _text(cell)
        if key is None or is_placeholder(value):
            continue
        if key == "area":
            # "DIỆN TÍCH" không nói rõ đất hay sàn -> lưu floor_area_m2 (giả định, xem DECISIONS)
            rec["floor_area_m2"] = parse_area_m2(value)
        elif key == "location":
            links = [_text(a) for a in cell.select("a")] or split_list(value)
            for loc in links:
                prov = normalize_province(loc)
                if prov:
                    rec["province"] = prov
                    break
            rec["extra"]["location"] = value
        elif key == "contractor":
            for a in cell.select("a[href]"):
                cslug = contractor_slug_from_url(_abs(a.get("href")) or "")
                if cslug:
                    rec["contractors"].append({"slug": cslug, "name": _text(a), "role": "thi_cong",
                                               "profile_url": _abs(a.get("href"))})
        elif key == "completed_year":
            year = parse_year(value)
            if year:
                rec["extra"]["completed_year"] = year
                rec["status"] = "completed"
        elif key == "floors":
            rec["floors"] = parse_count(value)
        elif key == "type":
            rec["building_type"] = map_building_type(value) or rec["building_type"]
        elif key != "name":
            rec["extra"][key] = value

    for i, a in enumerate(soup.select(".project_slider a[data-fancybox]")):
        src = _abs(a.get("href"))
        if src:
            rec["images"].append({"url": src, "caption": name, "sort_order": i})
    intro = soup.select_one("#dz-sec-intro .project_editor_content")
    if intro is not None:
        desc = clean_text(intro.get_text(" ", strip=True))
        rec["description"] = desc or None
    rec["source_updated_at"] = page_modified_at(soup)
    return rec
