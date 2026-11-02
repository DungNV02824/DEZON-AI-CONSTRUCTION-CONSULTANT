import { Component, type ReactNode } from 'react';

/** Cô lập lỗi render của một UI block: block lỗi hiện thông báo, phần còn lại của widget vẫn chạy. */
export class BlockBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error('[dezon-widget] Lỗi hiển thị khối nội dung', error);
  }

  render() {
    if (this.state.failed) {
      return <p className="dz-note dz-note--warn">Xin lỗi, em chưa hiển thị được nội dung này.</p>;
    }
    return this.props.children;
  }
}
