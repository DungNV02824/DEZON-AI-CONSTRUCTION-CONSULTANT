import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { SuggestionsBlock } from '../../api/types';
import { renderWithWidget } from '../../test/utils';
import { BlockRenderer } from './BlockRenderer';

const block: SuggestionsBlock = {
  type: 'suggestions',
  title: 'Gợi ý cho anh/chị',
  options: [
    { label: 'Hồ sơ của Kiến Phong có những công trình nào?', value: 'Hồ sơ của Kiến Phong có những công trình nào?' },
    { label: 'Khái toán này chưa bao gồm những chi phí nào?', value: 'Khái toán này chưa bao gồm những chi phí nào?' },
  ],
};

describe('Suggestions — gợi ý câu hỏi tiếp theo', () => {
  it('hiện tiêu đề + nút; bấm gửi câu hỏi như tin nhắn của khách', () => {
    const { actions } = renderWithWidget(<BlockRenderer block={block} active />);
    expect(screen.getByRole('group', { name: 'Gợi ý cho anh/chị' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hồ sơ của Kiến Phong có những công trình nào?' }));
    expect(actions.send).toHaveBeenCalledWith('Hồ sơ của Kiến Phong có những công trình nào?');
  });

  it('tin nhắn cũ (không còn là lượt mới nhất) -> không hiện gợi ý', () => {
    renderWithWidget(<BlockRenderer block={block} active={false} />);
    expect(screen.queryByRole('group', { name: 'Gợi ý cho anh/chị' })).toBeNull();
  });

  it('đang chờ trả lời -> nút bị khóa', () => {
    renderWithWidget(<BlockRenderer block={block} active />, { busy: true });
    expect(screen.getByRole('button', { name: 'Khái toán này chưa bao gồm những chi phí nào?' })).toBeDisabled();
  });
});
