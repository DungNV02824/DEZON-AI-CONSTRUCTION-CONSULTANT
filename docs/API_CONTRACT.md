# Hợp đồng API — Dezon AI Consultant (MVP)

Nguồn sự thật cho kiểu dữ liệu: Pydantic trong `backend/app/schemas/` và `backend/app/evidence.py`.
Frontend sinh/viết tay type TypeScript tương ứng trong `frontend/src/api/types.ts`.

Mọi số liệu hiển thị cho khách nằm trong `EvidenceValue`:

```json
{ "value": [4300000, 4700000], "display": "4,3–4,7 triệu/m²", "status": "on_dezon",
  "source": { "label": "Hồ sơ nhà thầu Kiến Phong", "url": "https://dezon.vn/...", "updated_at": "2026-10-31" },
  "is_stale": false }
```

Frontend **luôn hiển thị `display`** (không tự định dạng lại `value`), kèm badge `status` (mục 4.4) và
tooltip nguồn (`source.label`, `source.url`, `source.updated_at`). `is_stale=true` → hiển thị cảnh báo
"Dữ liệu cập nhật ngày dd/mm/yyyy, có thể đã cũ". `status="unavailable"` → chữ xám "Chưa có dữ liệu".

## Chat

| Method | Path | Body | Trả về |
|---|---|---|---|
| POST | `/api/chat/sessions` | `{user_id?}` | `{session_id, stage, slots, messages: ChatMessageOut[]}` — có sẵn tin chào + `quick_replies` |
| POST | `/api/chat/sessions/{id}/messages` | `{content: string}` | **SSE** (xem dưới) |
| GET | `/api/chat/sessions/{id}` | — | `{session_id, stage, slots, missing_required, messages: ChatMessageOut[]}` |

`ChatMessageOut = {id, role: "user"|"assistant", content, ui_blocks: UIBlock[], created_at}`

### SSE `POST /api/chat/sessions/{id}/messages`

Content-Type `text/event-stream`. Các event theo thứ tự:

```
event: text        data: {"delta": "Dạ, "}             # nhiều lần; nối lại thành câu trả lời
event: ui_block    data: {"block": UIBlock}            # 0..n lần, dựng từ kết quả tool
event: done        data: {"message_id": 123, "stage": "collect", "slots": {...}, "missing_required": [...]}
event: error       data: {"message": "..."}            # thay cho done khi lỗi
```

Văn bản chỉ được stream **sau khi** validator (mục 2.1) duyệt, nên `text` là câu trả lời đã an toàn.
Bấm nút `quick_replies` / `summary_confirm` = gửi `value` của nút như một tin nhắn mới.

`UIBlock.type` ∈ `quick_replies | summary_confirm | estimate_table | budget_options | contractor_list |
comparison_table | contractor_detail | source_badges | quote_request_form` — xem `app/schemas/ui_blocks.py`.

## Khái toán

| Method | Path | Body | Trả về |
|---|---|---|---|
| POST | `/api/estimates` | `EstimateInput & {chat_session_id?}` | `EstimateResult` (đã lưu, có `estimate_session_id`) |
| GET | `/api/estimates/{id}` | — | `EstimateResult` |
| POST | `/api/estimates/{id}/budget-adjustments` | `{target_budget_vnd}` | `BudgetAdjustmentResult` |
| GET | `/api/rates?building_type=&province=&work_scope=&quality_tier=` | — | `{rate: EvidenceValue, rate_data: RateData \| null}` |

## Nhà thầu

| Method | Path | Body | Trả về |
|---|---|---|---|
| GET | `/api/contractors?province=&building_type=&q=&limit=` | — | `{items: ContractorCard[], total}` |
| GET | `/api/contractors/{slug}` | — | `ContractorProfile` |
| POST | `/api/contractors/match` | `MatchCriteria & {chat_session_id?}` | `MatchResult` (ghi `recommendation_logs`) |
| POST | `/api/contractors/compare` | `{contractor_ids: int[2..5], estimate_session_id?, budget_vnd?}` | `ComparisonMatrix` — hàng "Mức phù hợp" chấm cùng tiêu chí với thẻ `contractor_list`: tỉnh/quận/loại/hạng mục/số tầng/ngân sách lấy từ slot phiên chat gắn với khái toán (`budget_vnd` trong body ưu tiên hơn) |
| POST | `/api/recommendations/{log_id}/click` | `{contractor_id}` | `{ok: true}` |

## Yêu cầu báo giá

| Method | Path | Body | Trả về |
|---|---|---|---|
| POST | `/api/quote-requests` | `{contractor_ids: int[1..5], estimate_session_id?, customer_name, customer_phone, customer_email?, note?}` | `{ids: uuid[], code: string}` |

## Admin (header `X-Admin-Token: $ADMIN_TOKEN`)

Thiếu/sai token → 401. `APP_ENV=production` mà `ADMIN_TOKEN` còn mặc định → 503 (trang quản trị bị khóa).
Lỗi trả `{detail: string}` tiếng Việt. Trang HTML: `GET /admin` (không cần token để tải trang; mọi API cần token).

| Method | Path | Body / tham số | Trả về |
|---|---|---|---|
| GET | `/api/admin/rates?building_type=&province=&work_scope=&quality_tier=&limit=&offset=` | — | `{items: RateOut[], total}` |
| GET | `/api/admin/rates/{id}` | — | `RateOut` |
| POST | `/api/admin/rates` | `{building_type, province ('*' = toàn quốc), work_scope, quality_tier?, price_min, price_max, effective_from, effective_to?}` | `RateOut` (201) |
| PUT | `/api/admin/rates/{id}` | `{province?, quality_tier?, price_min?, price_max?, effective_from?, effective_to?, clear_effective_to?, source_id?}` | `RateOut` |
| DELETE | `/api/admin/rates/{id}` | — | `{ok: true}` (409 nếu đơn giá đang được khái toán tham chiếu) |
| POST | `/api/admin/rates/{id}/approve` | `{verified_by?}` | Đánh dấu đơn giá là dữ liệu Dezon đã xác minh |
| GET | `/api/admin/rules?code=&approved=&limit=&offset=` | — | `{items: RuleOut[], total}` |
| POST | `/api/admin/rules` | `{code, coefficient, group_name?, label_vi?, base?, is_default?, approved?}` | `RuleOut` (201) — tạo phiên bản mới |
| PUT | `/api/admin/rules/{id}` | `{label_vi?, is_default?, approved?, coefficient?, group_name?, base?}` | `RuleOut` |
| POST | `/api/admin/rules/{id}/approve` | — | `RuleOut` (bật `approved`) |
| DELETE | `/api/admin/rules/{id}` | — | `{ok: true}` |
| GET | `/api/admin/weights` | — | `{items: WeightsOut[]}` |
| POST | `/api/admin/weights` | `{weights: {location, building_type, budget_fit, experience, similar_projects, profile_quality}, note?, activate?}` | `WeightsOut` (201) — tổng trọng số = 1 |
| PUT | `/api/admin/weights/{version}` | `{is_active?, note?}` | `WeightsOut` |
| POST | `/api/admin/weights/{version}/activate` | — | `WeightsOut` (chỉ một phiên bản hoạt động) |
| DELETE | `/api/admin/weights/{version}` | — | `{ok: true}` |
| GET | `/api/admin/sources?status=&source_type=&limit=&offset=` | — | `{items: SourceOut[], total}` |
| POST | `/api/admin/sources/{id}/verify` | `{verified_by?}` | `SourceOut` (status `verified`) |
| GET | `/api/admin/guardrail-violations?limit=&offset=&rule=` | — | `{items, total}` |
| GET | `/api/admin/metrics` | — | `MetricsReport` (mục 12) |
| POST | `/api/admin/import/{rates\|rules}` | multipart `file` (.csv/.xlsx) hoặc thân thô + `?filename=`, hoặc JSON `{path}` (file trong `IMPORT_DIR`); tham số `dry_run`, `force` | `ImportReport` `{kind, filename, total_rows, inserted, updated, unchanged, skipped, error_count, errors[{row, field, message}], source_id, status: ok\|partial\|failed\|unchanged, dry_run}`. `dry_run=true`: không ghi DB nhưng báo đúng số dòng **sẽ** thêm/cập nhật/giữ nguyên/bị chặn (`skipped` = bị nguồn ưu tiên cao hơn chặn). File Excel hỏng → `status: failed` |
| POST | `/api/admin/sync/csv` | như `import`, thêm `kind=rates\|rules` | `{status: "done", source: "csv", report: ImportReport}` |
| POST | `/api/admin/sync/{api\|crawl\|reindex\|after_sync}` | `?max_pages=1..1000` (api, crawl) | `{status: "queued", source, job, job_id, params, warning?}`; Redis chưa sẵn sàng → 503 |
| GET | `/api/admin/sync/jobs/{job_id}` | — | `{job_id, job, status: queued\|started\|finished\|failed\|…, status_vi, enqueued_at, started_at, ended_at, result, error}` — `result` là thống kê job (vd `{reindexed_chunks}`, `{profile_completeness, reindexed_chunks}`, `{status: "skipped", reason}`); `error` là dòng cuối traceback. Kết quả giữ 7 ngày; không có/hết hạn → 404; Redis lỗi → 503 |

`after_sync` = tính lại `profile_completeness` + re-embed toàn bộ; `reindex` = chỉ re-embed.

## Lỗi chung (mọi endpoint)

- 422 `{detail: "Dữ liệu không hợp lệ — <trường>: <lý do>", errors?: [...]}` — `detail` luôn là chuỗi tiếng Việt,
  không lặp lại dữ liệu khách gửi (kể cả router chat).
- Path, query hoặc body JSON chứa ký tự điều khiển (byte NUL `\u0000`, C0 trừ tab/xuống dòng, DEL) → 422
  "nội dung chứa ký tự điều khiển không được hỗ trợ", chặn trước khi chạm DB.

## Nhúng widget

```html
<script src="https://<host>/dezon-widget.js" data-api-base="https://<api-host>" defer></script>
```

Widget tự gắn nút chat nổi góc phải dưới; mở ra khung chat (toàn màn hình khi viewport < 640 px,
hỗ trợ tối thiểu 360 px). CSS được cô lập (Shadow DOM) để không ảnh hưởng trang dezon.vn.
