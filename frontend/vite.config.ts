/// <reference types="vitest" />
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { loadEnv, type Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

const root = fileURLToPath(new URL('.', import.meta.url));

function escapeAttr(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

/**
 * Khi build: phát hành thêm dist/index.html (trang demo giả lập dezon.vn) từ ./index.html,
 * thay đoạn nạp widget dạng module (chỉ dùng cho dev server) bằng thẻ <script> nhúng thật.
 * Không có VITE_API_BASE_URL -> trang demo bật chế độ giả lập (data-mock="true").
 */
function demoPagePlugin(apiBase: string): Plugin {
  // index.html: trang demo widget nổi; planning.html: trang demo giao diện mở rộng (data-mode="page").
  const pages: Array<{ file: string; extra: string }> = [
    { file: 'index.html', extra: '' },
    // Không truyền data-login-url: chủ sản phẩm bỏ nút "Đăng nhập / Đăng ký" ở thanh bên.
    { file: 'planning.html', extra: ' data-mode="page"' },
  ];
  return {
    name: 'dezon-demo-page',
    apply: 'build',
    generateBundle() {
      const attrs = apiBase ? `data-api-base="${escapeAttr(apiBase)}"` : 'data-mock="true"';
      for (const p of pages) {
        const html = readFileSync(`${root}${p.file}`, 'utf8');
        const tag = `<script src="./dezon-widget.js" ${attrs}${p.extra} defer></script>`;
        const out = html.replace(/<!-- dezon-widget:start -->[\s\S]*?<!-- dezon-widget:end -->/, tag);
        this.emitFile({ type: 'asset', fileName: p.file, source: out });
      }
    },
  };
}

export default defineConfig(({ mode, command }) => {
  const env = loadEnv(mode, root, 'VITE_');
  const apiBase = (env.VITE_API_BASE_URL ?? '').trim();
  return {
    plugins: [react(), demoPagePlugin(apiBase)],
    // React trong chế độ thư viện tham chiếu process.env.NODE_ENV — thay tĩnh để chạy trên trình duyệt.
    define: command === 'build' ? { 'process.env.NODE_ENV': JSON.stringify('production') } : {},
    build: {
      outDir: 'dist',
      emptyOutDir: true,
      target: 'es2019',
      sourcemap: false,
      lib: {
        entry: `${root}src/main.tsx`,
        name: 'DezonWidget',
        formats: ['iife'],
        fileName: () => 'dezon-widget.js',
      },
      rollupOptions: {
        output: { inlineDynamicImports: true },
      },
    },
    server: {
      port: 5173,
      // Dev: chuyển /api sang backend FastAPI khi không đặt VITE_API_BASE_URL.
      proxy: { '/api': { target: env.VITE_DEV_PROXY_TARGET || 'http://localhost:8000', changeOrigin: true } },
    },
    preview: { port: 4173 },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      css: false,
    },
  };
});
