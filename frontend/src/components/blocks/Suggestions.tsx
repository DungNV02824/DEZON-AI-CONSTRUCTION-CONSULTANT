import type { SuggestionsBlock } from '../../api/types';
import { useWidget } from '../../state/context';

/**
 * Gợi ý câu hỏi tiếp theo theo ngữ cảnh hội thoại (cuối lượt trả lời, khi luồng chính đã xong).
 * Bấm -> gửi `value` như tin nhắn của khách. Chỉ hiện ở tin nhắn mới nhất (như quick replies).
 */
export function Suggestions({ block, active }: { block: SuggestionsBlock; active: boolean }) {
  const { send, busy } = useWidget();
  if (!active || block.options.length === 0) return null;
  const title = block.title || 'Gợi ý cho anh/chị';
  return (
    <div className="dz-suggest" role="group" aria-label={title}>
      <p className="dz-suggest__title">
        <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
          <path
            fill="currentColor"
            d="M9 21h6v-1H9v1zm3-19a7 7 0 0 0-4 12.74V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.26A7 7 0 0 0 12 2z"
          />
        </svg>
        {title}
      </p>
      <div className="dz-suggest__list">
        {block.options.map((o, i) => (
          <button key={`${o.value}-${i}`} type="button" className="dz-suggest__btn" disabled={busy} onClick={() => send(o.value)}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
