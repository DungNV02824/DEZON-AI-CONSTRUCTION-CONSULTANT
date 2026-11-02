# DATA_MAPPING — Ánh xạ nguồn dữ liệu Dezon → schema (đặc tả mục 11)

Tài liệu này mô tả cách dữ liệu từ dezon.vn (crawler), Dezon API (khi có) và file CSV/XLSX được đưa vào
các bảng ở mục 4. Mã: `backend/app/ingestion/`.

## 1. Khảo sát trang thật (02/11/2026)

- Truy cập bằng user-agent `DezonAIBot/0.1`, đọc `robots.txt` trước, delay ≥ 2 giây, tổng cộng 9 request
  (robots.txt 2 lần + 7 trang). Trang **render phía server** (WordPress, theme `decox`) → **không cần Playwright**.
- `robots.txt` (bản sao: `backend/tests/fixtures/dezon/robots.txt`) với `User-agent: *`:
  `Allow: /`, nhưng `Disallow: /planning/`, `/*?auto_msg=`, `/*trigger_sso=`, `/wp-json/decox/v1/project-view`,
  `/*?project_cat_filter=`, `/*?project_region=` (và dạng `&…`). Một số bot AI có tên riêng bị chặn
  `/wp-content/uploads/`; DezonAIBot thuộc nhóm `*` nên không bị chặn, nhưng crawler **chỉ lưu URL ảnh, không tải ảnh**.
  → `/planning/` (khái toán của Dezon) **không được crawl**; bảng đơn giá/hệ số đi qua CSV/XLSX.
- Lưu ý: `urllib.robotparser` dùng "khớp đầu tiên" và không hỗ trợ wildcard nên với file này nó sẽ cho phép
  `/planning/`. Fetcher đánh giá thêm theo RFC 9309 (khớp dài nhất, `*`, `$`) và chỉ tải khi cả hai cùng cho phép.
- Trang danh sách nhà thầu có hộp "Đăng nhập để xem tiếp" hiển thị bằng JS (`#dz-ctwall`); nội dung hồ sơ vẫn
  có trong HTML công khai. Cần Dezon xác nhận việc dùng dữ liệu này (xem DECISIONS → Ingestion).

### Mẫu URL thật

| Loại trang | Mẫu URL | Ví dụ thật |
|---|---|---|
| Danh sách nhà thầu | `/nha-thau/` | https://dezon.vn/nha-thau/ (12 thẻ nổi bật + 295 thẻ thương hiệu) |
| Nhóm nhà thầu | `/nha-thau/<nhóm>/` (`tiem-nang`, `go`, `mep`, …) | https://dezon.vn/nha-thau/tiem-nang/ (41 nhà thầu) |
| Hồ sơ nhà thầu | `/nha-thau/<slug>/` (có `/` cuối) | https://dezon.vn/nha-thau/kien-phong/ · https://dezon.vn/nha-thau/smartcons-tech/ · https://dezon.vn/nha-thau/huy-hoang/ |
| Công trình thực tế của nhà thầu | `/nha-thau/<slug>/<công-trình>/` | https://dezon.vn/nha-thau/kien-phong/france-indochine-22-ha-noi/ · https://dezon.vn/nha-thau/kien-phong/green-pool-villa-vinh-long/ · https://dezon.vn/nha-thau/kien-phong/indochine-villa-quan-7-sai-gon/ |
| Danh sách dự án | `/du-an/`, phân trang `/du-an/page/<N>/` (104 trang) | https://dezon.vn/du-an/ |
| Trang dự án | `/du-an/<slug>/` | https://dezon.vn/du-an/villa-vinh-cuu/ |

So với seed dev (`app/db/seed.py`): slug nhà thầu `kien-phong`, `smartcons-tech`, `huy-hoang` **đúng với trang thật**,
nhưng URL thật có `/` ở cuối. Ba dự án mẫu của Kiến Phong thật ra nằm dưới hồ sơ nhà thầu
(`/nha-thau/kien-phong/<slug>/`), không phải `/du-an/<slug>/`; slug thật của dự án thứ ba là
`indochine-villa-quan-7-sai-gon` (seed đang dùng `indochine-villa-quan-7`).

Quan sát ngày 02/11/2026 khác với bảng 11.3 của đặc tả: Huy Hoàng hiện có 22 năm kinh nghiệm, đơn giá thô
4,2–4,8 triệu/m², 7 công trình (đặc tả mô tả là hồ sơ thiếu dữ liệu, 6 công trình). Seed giữ nguyên theo đặc tả
vì đó là fixture test cho trường hợp thiếu dữ liệu.

Fixture HTML (đã lược script/style/svg/header/footer, giữ nguyên cấu trúc): `backend/tests/fixtures/dezon/`
`contractor_list.html`, `contractor_list_tiem_nang.html`, `contractor_kien_phong.html`, `contractor_huy_hoang.html`,
`contractor_work_indochine_villa_quan_7.html`, `project_list.html`, `project_villa_vinh_cuu.html`.

## 2. HTML → dict trung gian → cột DB

Parser: `app/ingestion/crawler/parsers.py`. Parse số: `app/ingestion/parse_vi.py`.
Trường không có trên trang → `None` (hiển thị "Chưa có dữ liệu"); không suy diễn số liệu.

### 2.1 Thẻ trong danh sách nhà thầu

| HTML | Trường | Cột DB |
|---|---|---|
| `article[data-card] a.dznt-name` (text, href) | `name`, `profile_url`, `slug` | `contractors.name`, `profile_url`, `slug`, `external_id` (= slug) |
| `/nha-thau/`: `article > p` đầu; `/nha-thau/tiem-nang/`: phần đầu của `h3 + p` trước dấu `·` | `province` (chuẩn hóa) | `contractors.province` |
| `<strong>N</strong> công trình` | `dezon_project_count` | `contractors.dezon_project_count` ("công trình ghi nhận trên Dezon") |
| `<strong>N</strong> năm` (trang nhóm; `0 năm` = chưa khai báo → bỏ qua) | `years_experience` | `contractors.years_experience` |
| `h3 + p` hoặc các `span` tag / `data-tags` | `building_types` | `contractor_capabilities.building_type` (chỉ khi chưa có năng lực từ hồ sơ) |
| Tiêu đề khối (`div#tiem-nang h2`) hoặc `h1` của trang nhóm | `badge` | `contractors.badge` |
| `a.dzct-card`: `.dzct-name`, `.dzct-cnt b`, `data-cats`, `.dzct-cover[data-bg]` | `name`, `dezon_project_count`, `building_types`, `cover_url` | như trên (`cover_url` không lưu) |

Nhãn "Đề xuất" (`span[data-verified]`) trên thẻ **không** được hiểu là "đã xác minh".

Thẻ danh sách được ghi với `primary=False`: chỉ điền cột đang trống hoặc cột chính trang danh sách đã cung cấp trước
đó. Cột đang thuộc trang hồ sơ (hoặc nguồn khác cùng/ưu tiên cao hơn) giữ nguyên; trang hồ sơ là nguồn chính.

### 2.2 Hồ sơ nhà thầu `/nha-thau/<slug>/`

| HTML | Ví dụ (Kiến Phong) | Cột DB |
|---|---|---|
| `main h1` | Kiến Phong | `name` |
| Breadcrumb `a[href=/nha-thau/<nhóm>/]` | Nhà thầu tiềm năng | `badge` |
| `section > div > span` "N dự án trên Dezon" | 7 | `dezon_project_count` |
| `section > div > span > strong` (khu vực) | TP. HCM → `TP. Hồ Chí Minh` | `province` |
| `.dznt-sr` "Năm thành lập" | 2018 | `founded_year` |
| "Đơn giá thô" | Từ 4,3 đến 4,7 tr/m² → 4300000–4700000 | `rough_price_min/max` |
| "Đơn giá hoàn thiện cơ bản" | Từ 7,3 đến 9,5 tr/m² | `finish_price_min/max` |
| "Số lượng nhân sự" | 20 thợ · 12 văn phòng | `staff_workers`, `staff_office` (phần là khoảng "100 - 300 người" → `None`) |
| "Loại hình công trình" | Nhà phố · Biệt thự · Căn hộ · Công trình khác | `contractor_capabilities.building_type` |
| "Số năm kinh nghiệm" | 8 năm | `years_experience` |
| "Quy mô dự án" | 25 tỷ / công trình | `typical_project_value` (khoảng "7 – 15 tỷ" → `None`, lưu khoảng ở `extra`) |
| "Khu vực đã và đang thi công" | TP. HCM. | `contractor_capabilities.province` |
| "Bảo hành" (nếu có dòng này) | — | `has_warranty_info=true`, `warranty_text`; không có dòng → `None` |
| "Vai trò", "Vận hành" | Tổng thầu; Nuôi quân, Công nhật | chưa có cột → `extra` (không lưu) |
| `section#gioi-thieu p` | đoạn giới thiệu | `description` |
| `meta[property=og:image]` | ảnh công trình đầu | `avatar_url` |
| `meta[property=article:modified_time]` | 2026-08-26 | `data_sources.source_updated_at` |
| `section#cong-trinh a` | 7 công trình (tên, URL, ảnh) | hàng đợi crawl trang công trình |

`contractor_capabilities.work_scope` để trống: trang không có trường phạm vi công việc chuẩn hóa.

### 2.3 Trang công trình của nhà thầu `/nha-thau/<slug>/<công-trình>/`

| HTML | Cột DB |
|---|---|
| `main h1` | `projects.name` |
| URL | `projects.project_url`, `external_id` = `nha-thau/<slug>/<công-trình>`, `slug` = `<slug>--<công-trình>` |
| Dòng `.dznt-work-head + p`: "… do <a>Kiến Phong</a> thi công · TP. HCM · 8 ảnh … · Đăng ngày 22/10/2026" | `project_contractors (role=thi_cong)`, `projects.province`; ngày đăng chỉ lưu trong `extra` |
| `a.dznt-shot[href]` | `project_images.url` (caption = alt), `sort_order` |
| Tên chứa từ khóa rõ ràng (Villa/Biệt thự, Duplex/Căn hộ, Nhà phố…) | `projects.building_type`; không có từ khóa → `None` |

### 2.4 Trang dự án `/du-an/<slug>/`

| HTML | Cột DB |
|---|---|
| `h1` | `projects.name`; `slug` = slug URL; `external_id` = `du-an/<slug>` |
| `.breadcrumb_last` (Biệt Thự, Nhà Phố…) | `projects.building_type` |
| `table.jtable` "DIỆN TÍCH" (600m2) | `projects.floor_area_m2` (giả định — trang không nói rõ đất hay sàn) |
| "ĐỊA ĐIỂM" (link `/khu-vuc/…`) | `projects.province` |
| "NHÀ THẦU THI CÔNG" (link `/nha-thau/<slug>/`) | `project_contractors (role=thi_cong)` — chỉ liên kết nhà thầu đã có trong DB |
| "NĂM HOÀN THIỆN" | `projects.status = 'completed'` (năm lưu ở `extra`; `end_date` để trống vì chỉ có năm) |
| "CÔNG TY" (`/studio/…`), "NHIẾP ẢNH GIA" | `extra` (studio thiết kế không phải nhà thầu thi công) |
| `.project_slider a[data-fancybox]` | `project_images` |
| `#dz-sec-intro .project_editor_content` | `projects.description` |

### 2.5 `data_sources`

Mỗi trang = 1 bản ghi (khóa `url`, unique): `source_type=crawl_public` → `status=on_dezon`, `label`
("Hồ sơ nhà thầu Kiến Phong", "Công trình … (Kiến Phong) trên Dezon", "Dự án … trên Dezon", "Danh sách nhà thầu trên Dezon"),
`fetched_at` (lần kiểm tra gần nhất), `source_updated_at` (`article:modified_time`),
`raw_hash` = sha256 của HTML đã chuẩn hóa (bỏ script/style/comment/header/footer/lượt xem, ưu tiên vùng `<main>`).
`contractors.field_sources = {"rough_price_min": <data_source_id>, …}`.

### 2.6 Dict trung gian

```text
contractor: kind, slug, external_id, name, badge, province, founded_year, years_experience, staff_workers,
            staff_office, rough_price_min/max, finish_price_min/max, typical_project_value, dezon_project_count,
            has_warranty_info, warranty_text, description, avatar_url, profile_url,
            building_types[], service_provinces[], work_scopes[]?, works[{slug,name,url,thumb_url}],
            source_updated_at, extra{}
project:    kind, slug, external_id, name, building_type, province, district, floors, land_area_m2,
            floor_area_m2, start_date, end_date, status, description, project_url,
            images[{url,caption,sort_order}], contractors[{slug,name,profile_url,role}], source_updated_at, extra{}
```

Snapshot (`app/db/seed_data/dezon_snapshot.json`, tạo bằng
`python -m app.ingestion.crawler.runner --max-pages 30 --snapshot app/db/seed_data/dezon_snapshot.json`)
= `{"contractors": [dict + "_source": {url, fetched_at, raw_hash}], "projects": [...]}`; nạp khi seed với `SEED_SNAPSHOT=1`.

## 3. Dezon API → schema (dự kiến — chờ Dezon cung cấp tài liệu)

`app/ingestion/api_sync.py` (`source_type=dezon_api`, ưu tiên cao nhất). Đây là **giả định** cần thay khi có schema thật:

| Endpoint / trường API (giả định) | Dict trung gian / cột DB |
|---|---|
| `GET {DEZON_API_BASE_URL}/contractors?page=N` → `{data: [...], next_page}` | — |
| `id` / `slug` | `external_id` / `slug` |
| `name`, `badge` hoặc `tier_label`, `province` hoặc `city` | `name`, `badge`, `province` |
| `founded_year`, `years_experience`, `staff.workers`, `staff.office` | cột cùng tên |
| `rough_price_min/max` (hoặc `rough_price_text` parse bằng parse_vi) | `rough_price_min/max` |
| `finish_price_min/max` (hoặc `finish_price_text`) | `finish_price_min/max` |
| `typical_project_value` (hoặc `typical_project_value_text`) | `typical_project_value` |
| `project_count` | `dezon_project_count` |
| `warranty` / `warranty_text` | `has_warranty_info`, `warranty_text` |
| `building_types[]` / `project_types[]`, `service_areas[]` / `provinces[]` | `contractor_capabilities` |
| `profile_url` (mặc định `{DEZON_SITE_URL}/nha-thau/<slug>/`), `avatar_url` / `logo`, `updated_at` | `profile_url`, `avatar_url`, `data_sources.source_updated_at` |
| `GET /projects?page=N`: `id`, `slug`, `name`, `building_type`, `province`, `district`, `floors`, `land_area_m2`, `floor_area_m2`, `start_date`, `end_date`, `status`, `description`, `url`, `images[]`, `contractors[{id,slug,name,role,profile_url}]` | `projects`, `project_images`, `project_contractors` (`external_id` = `api:<id>`) |

Mỗi bản ghi API → 1 `data_sources` với `url` = URL tài nguyên API (`{base}/contractors/<id>`), `raw_hash` = sha256
JSON bản ghi. Cờ `is_verified` từ API không tự nâng trạng thái lên `verified` (việc này do admin duyệt).

## 4. CSV/XLSX → `construction_rates`, `estimate_rules`

`app/ingestion/csv_import.py`; mẫu: `docs/samples/construction_rates.csv`, `docs/samples/estimate_rules.csv`
(UTF-8 có BOM — mở trực tiếp bằng Excel không lỗi dấu). Phân cách `,` hoặc `;`; XLSX đọc sheet đầu.

| File | Cột | Ràng buộc |
|---|---|---|
| construction_rates | `building_type, province, work_scope, quality_tier, price_min, price_max, effective_from, effective_to` | enum hợp lệ (mã hoặc nhãn tiếng Việt: "Biệt thự", "Xây thô"…); `province` = tên tỉnh hoặc `*` / "Toàn quốc"; giá là số đồng (`3400000`, `3.400.000`) hoặc "4,0 triệu"; `0 < price_min ≤ price_max`; ngày ISO hoặc dd/mm/yyyy; `effective_to ≥ effective_from` |
| estimate_rules | `code, group_name, label_vi, coefficient, base, is_default, version` | `group_name` ∈ mong/tang/ham/mai/san_thuong/ban_cong/san; `base` ∈ floor_area/mezzanine_area/ground_floor_area/basement_area/roof_area/rooftop_area/yard_area/balcony_area; `0 < coefficient < 100`; `version ≥ 1`; luôn nhập với `approved=false` |

Mỗi lần nhập = 1 `data_sources` (`csv_import` → `reference`, `raw_hash` = sha256 file; nhập lại cùng file → "unchanged").
Bản ghi cùng khóa đang thuộc nguồn ưu tiên cao hơn (vd đơn giá seed `dezon_editorial`) bị bỏ qua trừ khi `force=True`.
Số liệu trong file mẫu chép từ đơn giá/hệ số seed dev (dữ liệu tham khảo), không phải bảng giá chính thức.
