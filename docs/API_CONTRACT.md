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
| POST | `/api/contractors/compare` | `{contractor_ids: int[2..5], estimate_session_id?}` | `ComparisonMatrix` |
| POST | `/api/recommendations/{log_id}/click` | `{contractor_id}` | `{ok: true}` |

## Yêu cầu báo giá

| Method | Path | Body | Trả về |
|---|---|---|---|
| POST | `/api/quote-requests` | `{contractor_ids: int[1..5], estimate_session_id?, customer_name, customer_phone, customer_email?, note?}` | `{ids: uuid[], code: string}` |

## Admin (header `X-Admin-Token: $ADMIN_TOKEN`)

`GET/POST/PUT/DELETE /api/admin/rates`, `/api/admin/rules`, `/api/admin/weights`;
`POST /api/admin/sources/{id}/verify`; `GET /api/admin/guardrail-violations`; `GET /api/admin/metrics`;
`POST /api/admin/sync/{source}` (`csv` | `api` | `crawl` | `reindex`).

## Nhúng widget

```html
<script src="https://<host>/dezon-widget.js" data-api-base="https://<api-host>" defer></script>
```

Widget tự gắn nút chat nổi góc phải dưới; mở ra khung chat (toàn màn hình khi viewport < 640 px,
hỗ trợ tối thiểu 360 px). CSS được cô lập (Shadow DOM) để không ảnh hưởng trang dezon.vn.
