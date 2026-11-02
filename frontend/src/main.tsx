/**
 * Điểm vào widget nhúng:
 *   <script src="https://<host>/dezon-widget.js" data-api-base="https://<api-host>" defer></script>
 * Tùy chọn: data-open="true" (mở sẵn), data-mock="true" hoặc ?dezon_mock=1 (dữ liệu giả lập).
 * Widget gắn vào một Shadow DOM riêng để CSS không ảnh hưởng trang dezon.vn (và ngược lại).
 */
import { createRoot } from 'react-dom/client';
import { createApiClient } from './api';
import { App } from './components/App';
import { findWidgetScript, readConfig } from './config';
import css from './styles.css?inline';

// document.currentScript chỉ có giá trị khi script đang chạy đồng bộ — lưu lại ngay.
const currentScript = typeof document !== 'undefined' ? findWidgetScript(document.currentScript, document) : null;

function mount() {
  if (window.__DEZON_WIDGET_MOUNTED__) return;
  window.__DEZON_WIDGET_MOUNTED__ = true;

  const config = readConfig(currentScript, window.location.search, import.meta.env.VITE_API_BASE_URL);
  const host = document.createElement('div');
  host.id = 'dezon-ai-widget';
  host.setAttribute('data-dezon-widget', '');
  document.body.appendChild(host);

  const shadow = host.attachShadow({ mode: 'open' });
  const style = document.createElement('style');
  style.textContent = css;
  shadow.appendChild(style);
  const container = document.createElement('div');
  shadow.appendChild(container);

  const client = createApiClient(config);
  createRoot(container).render(
    <App
      client={client}
      initiallyOpen={config.open}
      registerControls={(c) => {
        window.DezonWidget = { ...c, config };
      }}
    />,
  );
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
else mount();
