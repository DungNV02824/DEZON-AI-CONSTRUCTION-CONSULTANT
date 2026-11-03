# Dezon AI Consultant — widget frontend

React 18 + TypeScript (strict), build bằng Vite thành **một file IIFE** `dist/dezon-widget.js`
(CSS nhúng trong bundle, chèn vào Shadow DOM để cô lập style với dezon.vn).

## Nhúng vào dezon.vn

```html
<script src="https://<host>/dezon-widget.js" data-api-base="https://<api-host>" defer></script>
```

| Thuộc tính | Ý nghĩa |
|---|---|
| `data-api-base` | Gốc URL backend FastAPI. Không đặt -> dùng `VITE_API_BASE_URL` lúc build; rỗng -> cùng origin (`/api/...`). |
| `data-open="true"` | Mở sẵn khung chat khi tải trang. |
| `data-mock="true"` | Dữ liệu giả lập (không gọi backend) — chỉ để kiểm tra giao diện. Cũng bật được bằng `?dezon_mock=1`. |

Trang chủ có thể điều khiển widget: `window.DezonWidget.open() / close() / toggle()`.

## Lệnh

```bash
npm install
npm run dev        # http://localhost:5173/?dezon_mock=1  (dev server, /api proxy -> localhost:8000)
npm run typecheck
npm test           # vitest + Testing Library (jsdom)
npm run build      # dist/dezon-widget.js + dist/index.html (trang demo)
npm run preview    # http://localhost:4173/?dezon_mock=1
```

Docker: `docker build --build-arg VITE_API_BASE_URL=http://localhost:8000 -t dezon-frontend .`
→ nginx cổng 80 phục vụ `/dezon-widget.js` và trang chủ `/` = giao diện đầy đủ (`planning.html`, cũng ở `/planning`), trang demo widget nổi ở `/widget-demo`
(compose map ra `localhost:8080`).

## Cấu trúc

- `src/api/types.ts` — type khớp Pydantic (`backend/app/evidence.py`, `backend/app/schemas/*`).
- `src/api/sse.ts` — parser SSE cho luồng POST (fetch + ReadableStream).
- `src/api/client.ts` — client REST/SSE; `src/api/mock.ts` + `fixtures.ts` — chế độ giả lập (luồng 5.4).
- `src/state/useChat.ts` — phiên (session_id trong localStorage), stream, tin nhắn cục bộ.
- `src/components/blocks/*` — 9 UI block mục 8.1; `src/components/common/*` — `EvidenceCell`, `StatusBadge`,
  tooltip nguồn.

Nguyên tắc: mọi số liệu là `EvidenceValue` → luôn hiển thị `display` từ backend (không tự định dạng `value`);
thiếu dữ liệu → chữ xám đúng "Chưa có dữ liệu"; mọi bảng khái toán luôn có ghi chú miễn trừ.
