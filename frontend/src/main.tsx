/**
 * Điểm vào widget nhúng:
 *   <script src="https://<host>/dezon-widget.js" data-api-base="https://<api-host>" defer></script>
 * Tùy chọn: data-open="true" (mở sẵn), data-mock="true" hoặc ?dezon_mock=1 (dữ liệu giả lập).
 *
 * Giao diện đầy đủ "AI for Planning" (vd trang dezon.vn/planning/):
 *   <div id="dezon-planning" style="height: 100vh"></div>
 *   <script src=".../dezon-widget.js" data-mode="page" data-api-base="..." data-login-url="..." defer></script>
 * (data-target="<selector>" để gắn vào phần tử khác; có #dezon-planning trên trang thì tự bật data-mode="page").
 * Widget gắn vào một Shadow DOM riêng để CSS không ảnh hưởng trang dezon.vn (và ngược lại).
 */
import { createRoot } from 'react-dom/client';
import { createApiClient } from './api';
import { App } from './components/App';
import { findWidgetScript, readConfig } from './config';
import css from './styles.css?inline';
import pageCss from './styles.page.css?inline';

// document.currentScript chỉ có giá trị khi script đang chạy đồng bộ — lưu lại ngay.
const currentScript = typeof document !== 'undefined' ? findWidgetScript(document.currentScript, document) : null;

function mount() {
  if (window.__DEZON_WIDGET_MOUNTED__) return;
  window.__DEZON_WIDGET_MOUNTED__ = true;

  const config = readConfig(currentScript, window.location.search, import.meta.env.VITE_API_BASE_URL);
  const host = document.createElement('div');
  host.id = 'dezon-ai-widget';
  host.setAttribute('data-dezon-widget', '');
  if (config.mode === 'page') {
    // Gắn vào phần tử đích và lấp đầy nó; không có phần tử đích -> chiếm toàn màn hình.
    let target: Element | null = null;
    try {
      target = document.querySelector(config.target);
    } catch {
      target = null;
    }
    host.style.display = 'block';
    host.style.height = '100%';
    host.style.width = '100%';
    if (target) target.appendChild(host);
    else {
      host.style.position = 'fixed';
      host.style.inset = '0';
      host.style.zIndex = '2147483000';
      document.body.appendChild(host);
    }
  } else {
    document.body.appendChild(host);
  }

  const shadow = host.attachShadow({ mode: 'open' });
  const style = document.createElement('style');
  style.textContent = css + pageCss;
  shadow.appendChild(style);
  const container = document.createElement('div');
  if (config.mode === 'page') container.style.height = '100%';
  shadow.appendChild(container);

  const client = createApiClient(config);
  createRoot(container).render(
    <App
      client={client}
      initiallyOpen={config.open}
      mode={config.mode}
      loginUrl={config.loginUrl}
      registerControls={(c) => {
        window.DezonWidget = { ...c, config };
      }}
    />,
  );
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
else mount();
