import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { createMockClient } from '../api/mock';
import { readConfig } from '../config';
import { makeTitle, upsertHistory } from '../state/history';
import { App } from './App';

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  document.body.innerHTML = '';
});

describe('lịch sử "Recent chats"', () => {
  it('upsert đưa lên đầu, giữ tiêu đề cũ, rút gọn tiêu đề dài', () => {
    let items = upsertHistory([], 'a', 'Khái toán chi phí xây nhà', 1);
    items = upsertHistory(items, 'b', 'Câu khác', 2);
    items = upsertHistory(items, 'a', 'Tin nhắn thứ hai', 3);
    expect(items.map((i) => i.id)).toEqual(['a', 'b']);
    expect(items[0]!.title).toBe('Khái toán chi phí xây nhà');
    expect(makeTitle('x'.repeat(200)).length).toBe(80);
  });
});

describe('readConfig — giao diện mở rộng', () => {
  it('data-mode="page" + data-login-url; tự bật page khi trang có #dezon-planning', () => {
    const s = document.createElement('script');
    s.dataset.mode = 'page';
    s.dataset.loginUrl = 'https://dezon.vn/dang-nhap/';
    expect(readConfig(s, '', undefined)).toMatchObject({ mode: 'page', loginUrl: 'https://dezon.vn/dang-nhap/' });
    expect(readConfig(null, '', undefined).mode).toBe('widget');
    const el = document.createElement('div');
    el.id = 'dezon-planning';
    document.body.appendChild(el);
    expect(readConfig(null, '', undefined).mode).toBe('page');
    expect(readConfig(null, '?dezon_mode=widget', undefined).mode).toBe('widget');
  });
});

describe('App mode="page" (AI for Planning)', () => {
  it('thanh bên + thẻ câu hỏi từng bước; gửi tin -> Recent chats; New chat; mở lại cuộc trò chuyện cũ', async () => {
    const client = createMockClient({ delayMs: 0, storage: window.localStorage });
    render(<App client={client} mode="page" loginUrl="https://dezon.vn/dang-nhap/" />);

    expect(screen.getByText('DEZON', { selector: '.dz-plan__logo' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /New chat/ })).toBeTruthy();
    expect(screen.getByRole('link', { name: /Đăng nhập \/ Đăng ký/ }).getAttribute('href')).toBe('https://dezon.vn/dang-nhap/');
    expect(screen.getByText('Chưa có cuộc trò chuyện nào.')).toBeTruthy();

    // Lời chào + thẻ câu hỏi từng bước (question_step)
    const card = await waitFor(() => {
      const c = document.querySelector('.dz-qstep:not(.dz-qstep--done)');
      expect(c).toBeTruthy();
      return c as HTMLElement;
    });
    expect(within(card).getByRole('button', { name: 'Nhà phố' })).toBeTruthy();
    expect(screen.getByPlaceholderText('Hỏi bất kỳ điều gì')).toBeTruthy();

    fireEvent.click(within(card).getByRole('button', { name: 'Nhà phố' }));
    const item = await screen.findByRole('button', { name: /Nhà phố/, current: true });
    expect(item.className).toContain('dz-plan__item--active');
    await waitFor(() => expect(document.querySelector('.dz-qstep:not(.dz-qstep--done)')?.textContent).toMatch(/Khu vực xây dựng/));
    const firstSession = window.localStorage.getItem('dezon_ai_mock_session_id');

    // Cuộc trò chuyện mới: lịch sử giữ mục cũ (không còn active)
    fireEvent.click(screen.getByRole('button', { name: /New chat/ }));
    await waitFor(() => expect(window.localStorage.getItem('dezon_ai_mock_session_id')).not.toBe(firstSession));
    const list = () => within(document.querySelector('.dz-plan__list') as HTMLElement);
    const old = await list().findByRole('button', { name: /Nhà phố/ });
    expect(old.getAttribute('aria-current')).toBeNull();

    // Mở lại cuộc trò chuyện cũ từ Recent chats
    fireEvent.click(old);
    await waitFor(() => expect(window.localStorage.getItem('dezon_ai_mock_session_id')).toBe(firstSession));
    await waitFor(() => expect(screen.getByText('Nhà phố', { selector: '.dz-bubble--user' })).toBeTruthy());
  });

  it('Xóa tất cả xóa lịch sử', async () => {
    const client = createMockClient({ delayMs: 0, storage: window.localStorage });
    window.confirm = () => true;
    render(<App client={client} mode="page" />);
    const card = await waitFor(() => {
      const c = document.querySelector('.dz-qstep:not(.dz-qstep--done)');
      expect(c).toBeTruthy();
      return c as HTMLElement;
    });
    fireEvent.click(within(card).getByRole('button', { name: 'Biệt thự' }));
    await screen.findByRole('button', { name: /Biệt thự/, current: true });
    fireEvent.click(screen.getByRole('button', { name: /Xóa tất cả/ }));
    await waitFor(() => expect(screen.getByText('Chưa có cuộc trò chuyện nào.')).toBeTruthy());
    // Không cấu hình loginUrl -> ẩn nút đăng nhập
    expect(screen.queryByRole('link', { name: /Đăng nhập/ })).toBeNull();
  });
});

describe('widget nổi — nút "Mở rộng"', () => {
  it('mở rộng sang giao diện AI for Planning rồi thu nhỏ lại, giữ nguyên hội thoại', async () => {
    const client = createMockClient({ delayMs: 0, storage: window.localStorage });
    render(<App client={client} initiallyOpen />);
    await screen.findByText(/Xin chào anh\/chị/);
    fireEvent.click(screen.getByRole('button', { name: /Mở rộng/ }));
    const overlay = screen.getByRole('dialog', { name: 'Dezon AI for Planning' });
    expect(within(overlay).getByRole('button', { name: /New chat/ })).toBeTruthy();
    expect(within(overlay).getByText(/Xin chào anh\/chị/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Mở trợ lý Dezon AI/ })).toBeNull(); // ẩn nút nổi
    fireEvent.click(within(overlay).getByRole('button', { name: /Thu nhỏ/ }));
    expect(screen.queryByRole('dialog', { name: 'Dezon AI for Planning' })).toBeNull();
    expect(screen.getByRole('dialog', { name: 'Dezon AI Consultant' })).toBeTruthy();
  });
});
