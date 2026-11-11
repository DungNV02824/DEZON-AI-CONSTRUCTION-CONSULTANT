"""Ảnh cho slideshow: cover_images xoay vòng giữa các dự án (tối đa MAX_COVER_IMAGES = 80), gallery hồ sơ có
tên/link dự án, ảnh thu nhỏ dezon.vn (thumb_url) và số ảnh thực có của từng công trình (total_images)."""
from sqlalchemy import select

from app.db.models import Contractor, Project, ProjectContractor, ProjectImage
from app.images import derive_thumb_url
from app.schemas.matching import ImageRef, ProjectSummary
from app.services import contractor_service as svc


def _project(pid: int, n_images: int, *, reverse_sort: bool = False) -> Project:
    p = Project(id=pid, slug=f"du-an-{pid}", name=f"Dự án {pid}",
                project_url=f"https://dezon.vn/nha-thau/demo/du-an-{pid}/")
    for k in range(n_images):
        order = n_images - 1 - k if reverse_sort else k
        p.images.append(ProjectImage(id=pid * 100 + k, url=f"https://dezon.vn/img/{pid}-{order}.jpg",
                                     caption=f"Dự án {pid} — ảnh {order + 1}", sort_order=order))
    return p


def _contractor(*projects: Project) -> Contractor:
    c = Contractor(id=1, slug="demo", name="Demo")
    for p in projects:
        c.project_links.append(ProjectContractor(project=p))
    return c


# ---------------------------------------------------------------------------------------------------- thuần
def test_image_ref_new_fields_optional():
    ref = ImageRef(url="https://dezon.vn/a.jpg")
    assert ref.project_name is None and ref.project_url is None and ref.caption is None


def test_cover_images_round_robin_across_projects():
    # Thứ tự gắn dự án khác thứ tự id -> kết quả vẫn theo id dự án.
    c = _contractor(_project(3, 1), _project(1, 3), _project(2, 2))
    urls = [i.url for i in svc._cover_images(c)]
    assert urls == [
        "https://dezon.vn/img/1-0.jpg", "https://dezon.vn/img/2-0.jpg", "https://dezon.vn/img/3-0.jpg",
        "https://dezon.vn/img/1-1.jpg", "https://dezon.vn/img/2-1.jpg",
        "https://dezon.vn/img/1-2.jpg",
    ]


def test_cover_images_default_cap_covers_all_real_images():
    # Chủ sản phẩm muốn xem tối đa ảnh: giới hạn đủ cho nhà thầu nhiều ảnh nhất hiện nay (Smartcons Tech: 63 ảnh).
    assert svc.MAX_COVER_IMAGES >= 63
    c = _contractor(*[_project(pid, 8) for pid in range(1, 9)])  # 64 ảnh
    assert len(svc._cover_images(c)) == 64


def test_cover_images_capped_and_carry_project_info(monkeypatch):
    monkeypatch.setattr(svc, "MAX_COVER_IMAGES", 12)
    c = _contractor(*[_project(pid, 8, reverse_sort=True) for pid in range(1, 6)])
    covers = svc._cover_images(c)
    assert len(covers) == 12
    # 5 dự án × ảnh 1, 5 × ảnh 2, rồi 2 ảnh 3 (theo sort_order, không theo thứ tự thêm).
    assert [i.url.rsplit("/", 1)[1] for i in covers[:6]] == ["1-0.jpg", "2-0.jpg", "3-0.jpg", "4-0.jpg", "5-0.jpg",
                                                             "1-1.jpg"]
    assert [i.project_slug for i in covers[10:]] == ["du-an-1", "du-an-2"]
    first = covers[0]
    assert first.project_name == "Dự án 1" and first.project_url == "https://dezon.vn/nha-thau/demo/du-an-1/"
    assert first.caption == "Dự án 1 — ảnh 1"
    assert svc._cover_images(c) == covers  # cố định giữa các lần gọi


def test_cover_images_empty_and_caption_fallback():
    assert svc._cover_images(_contractor()) == []
    p = _project(7, 1)
    p.images[0].caption = None
    assert svc._cover_images(_contractor(p))[0].caption == "Dự án 7"


def test_project_image_refs_capped_at_20():
    refs = svc._image_refs(_project(9, 25))
    assert len(refs) == svc.MAX_PROJECT_IMAGES == 20
    assert all(r.project_name == "Dự án 9" and r.project_url.endswith("/du-an-9/") for r in refs)


# ---------------------------------------------------------------------------------------------------- DB
def test_card_and_profile_images_have_project_links(db):
    kp = db.scalar(select(Contractor).where(Contractor.slug == "kien-phong"))
    card = svc.build_contractor_card(db, kp)
    assert 1 <= len(card.cover_images) <= svc.MAX_COVER_IMAGES
    for img in card.cover_images:
        assert img.project_name and img.project_url.startswith("https://") and img.project_slug

    prof = svc.get_contractor_profile(db, "kien-phong")
    assert prof.images and prof.total_images == len(prof.images)
    for proj in prof.projects:
        for img in proj.images:
            assert img.project_name == proj.name and img.project_url == proj.project_url
            assert img.project_slug == proj.slug


def test_profile_total_images_counts_beyond_cap(db):
    kp = db.scalar(select(Contractor).where(Contractor.slug == "kien-phong"))
    proj = next(lk.project for lk in kp.project_links if lk.project is not None)
    before = svc.get_contractor_profile(db, "kien-phong").total_images
    for k in range(25):
        db.add(ProjectImage(project_id=proj.id, url=f"https://dezon.vn/img/extra-{k}.jpg", sort_order=100 + k))
    db.flush()
    db.expire_all()
    prof = svc.get_contractor_profile(db, "kien-phong")
    assert prof.total_images == before + 25
    shown = next(p for p in prof.projects if p.id == proj.id)
    assert len(shown.images) == svc.MAX_PROJECT_IMAGES
    assert len(prof.images) < prof.total_images


# ---------------------------------------------------------------------------------------------------- ảnh thu nhỏ
KP_ORIG = "https://dezon.vn/wp-content/uploads/dz-data/nt/kien-phong/o/UmjqbDGOsoPA8Sxm4xmliVZ7.jpg"
KP_THUMB = "https://dezon.vn/wp-content/uploads/dz-data/nt/kien-phong/UmjqbDGOsoPA8Sxm4xmliVZ7.jpg"


def test_derive_thumb_url_strict_pattern():
    assert derive_thumb_url(KP_ORIG) == KP_THUMB
    assert derive_thumb_url("https://www.dezon.vn/wp-content/uploads/dz-data/nt/huy-hoang/o/Ab_c-1.PNG") == (
        "https://www.dezon.vn/wp-content/uploads/dz-data/nt/huy-hoang/Ab_c-1.PNG")
    for url in (
        None, "", KP_THUMB,  # đã là ảnh thu nhỏ
        "https://dezon.vn/img/1-0.jpg",  # ngoài thư mục dz-data/nt
        "https://evil.example/wp-content/uploads/dz-data/nt/kien-phong/o/x.jpg",  # khác tên miền
        "https://dezon.vn.evil.example/wp-content/uploads/dz-data/nt/kien-phong/o/x.jpg",
        "https://dezon.vn/wp-content/uploads/dz-data/nt/kien-phong/o/sub/x.jpg",  # lồng thêm thư mục
        "https://dezon.vn/wp-content/uploads/dz-data/nt/kien-phong/o/x.jpg?w=300",  # có query
        "https://dezon.vn/wp-content/uploads/dz-data/nt/kien-phong/o/x.gif",  # đuôi lạ
        "https://dezon.vn/wp-content/uploads/2026/03/o/x.jpg",
    ):
        assert derive_thumb_url(url) is None, url


def test_image_ref_thumb_and_project_total_default():
    assert ImageRef(url=KP_ORIG).thumb_url is None
    assert ProjectSummary(id=1, slug="a", name="A", project_url="https://dezon.vn/a/").total_images == 0


def test_parse_work_page_reads_data_thumb():
    from pathlib import Path

    from app.ingestion.crawler import parsers

    html = (Path(__file__).parent / "fixtures" / "dezon" / "contractor_work_indochine_villa_quan_7.html").read_text(
        encoding="utf-8")
    rec = parsers.parse_contractor_work(html, "https://dezon.vn/nha-thau/kien-phong/indochine-villa-quan-7-sai-gon/")
    first = rec["images"][0]
    assert first["url"] == KP_ORIG and first["thumb_url"] == KP_THUMB
    assert all(img["thumb_url"] and "/o/" not in img["thumb_url"] for img in rec["images"])


def test_image_ref_thumb_from_column_or_derived():
    p = Project(id=5, slug="du-an-5", name="Dự án 5", project_url="https://dezon.vn/nha-thau/demo/du-an-5/")
    p.images.append(ProjectImage(id=1, url=KP_ORIG, sort_order=0))  # snapshot cũ: chưa có thumb_url
    p.images.append(ProjectImage(id=2, url="https://dezon.vn/img/x.jpg", sort_order=1))  # không khớp mẫu
    p.images.append(ProjectImage(id=3, url=KP_ORIG, sort_order=2, thumb_url="https://dezon.vn/thumb/3.jpg"))
    refs = svc._image_refs(p)
    assert [r.thumb_url for r in refs] == [KP_THUMB, None, "https://dezon.vn/thumb/3.jpg"]


def test_project_summary_total_images_counts_beyond_cap():
    p = _project(9, 25)
    c = _contractor(p)
    (summary,) = svc._project_summaries(c)
    assert len(summary.images) == svc.MAX_PROJECT_IMAGES and summary.total_images == 25


def test_sync_project_images_stores_thumbs_and_detects_thumb_change(db):
    from app.ingestion.upsert import sync_project_images

    p = Project(slug="du-an-thumb", name="Dự án thumb", project_url="https://dezon.vn/nha-thau/demo/du-an-thumb/")
    db.add(p)
    db.flush()
    imgs = [{"url": KP_ORIG, "caption": "ảnh 1", "sort_order": 0}]
    assert sync_project_images(db, p, imgs) is True
    assert p.images[0].thumb_url == KP_THUMB  # suy từ ảnh gốc khi nguồn không có data-thumb
    assert sync_project_images(db, p, imgs) is False
    assert sync_project_images(db, p, [{**imgs[0], "thumb_url": "https://dezon.vn/t/1.jpg"}]) is True
    assert p.images[0].thumb_url == "https://dezon.vn/t/1.jpg"


def test_profile_images_carry_thumbs_and_project_totals(db):
    prof = svc.get_contractor_profile(db, "kien-phong")
    assert prof.projects
    for proj in prof.projects:
        assert proj.total_images == len(proj.images)
        for img in proj.images:
            assert img.thumb_url is None or (img.thumb_url != img.url and "/o/" not in img.thumb_url)
    card = svc.build_contractor_card(db, db.scalar(select(Contractor).where(Contractor.slug == "kien-phong")))
    dezon_imgs = [i for i in card.cover_images if "/dz-data/nt/" in i.url and "/o/" in i.url]
    assert all(i.thumb_url == derive_thumb_url(i.url) for i in dezon_imgs)
