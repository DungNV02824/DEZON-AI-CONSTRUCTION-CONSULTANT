"""Ảnh cho slideshow: cover_images xoay vòng giữa các dự án (tối đa 12), gallery hồ sơ có tên/link dự án."""
from sqlalchemy import select

from app.db.models import Contractor, Project, ProjectContractor, ProjectImage
from app.schemas.matching import ImageRef
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
