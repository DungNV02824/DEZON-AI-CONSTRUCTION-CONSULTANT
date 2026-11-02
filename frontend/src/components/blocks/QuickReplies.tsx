import type { QuickRepliesBlock } from '../../api/types';
import { useWidget } from '../../state/context';

export function QuickReplies({ block, active }: { block: QuickRepliesBlock; active: boolean }) {
  const { send, focusInput, busy } = useWidget();
  if (!active) return null;
  const allowFree = block.allow_free_text !== false;
  return (
    <div className="dz-qr" role="group" aria-label="Lựa chọn nhanh">
      {block.options.map((o, i) => (
        <button key={`${o.value}-${i}`} type="button" className="dz-chip-btn" disabled={busy} onClick={() => send(o.value)}>
          {o.label}
        </button>
      ))}
      {allowFree && (
        <button type="button" className="dz-chip-btn dz-chip-btn--ghost" onClick={focusInput}>
          Nhập thông tin khác
        </button>
      )}
    </div>
  );
}
