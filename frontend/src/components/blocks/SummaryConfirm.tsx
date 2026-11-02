import type { SummaryConfirmBlock } from '../../api/types';
import { useWidget } from '../../state/context';

const DEFAULT_CONFIRM = { label: 'Đúng rồi', value: 'Đúng rồi, tính khái toán giúp tôi' };
const DEFAULT_EDIT = { label: 'Sửa lại', value: 'Tôi muốn sửa lại thông tin' };

export function SummaryConfirm({ block, active }: { block: SummaryConfirmBlock; active: boolean }) {
  const { send, busy } = useWidget();
  const confirm = block.confirm ?? DEFAULT_CONFIRM;
  const edit = block.edit ?? DEFAULT_EDIT;
  const disabled = !active || busy;
  return (
    <section className="dz-card" aria-label="Tóm tắt thông tin công trình">
      <h3 className="dz-card__title">Thông tin công trình</h3>
      <dl className="dz-kv">
        {block.items.map((it, i) => (
          <div className="dz-kv__row" key={`${it.label}-${i}`}>
            <dt>{it.label}</dt>
            <dd>{it.value}</dd>
          </div>
        ))}
      </dl>
      {block.assumptions && block.assumptions.length > 0 && (
        <div className="dz-note">
          <strong>Giả định</strong>
          <ul>
            {block.assumptions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="dz-actions">
        <button type="button" className="dz-btn dz-btn--primary" disabled={disabled} onClick={() => send(confirm.value)}>
          {confirm.label}
        </button>
        <button type="button" className="dz-btn" disabled={disabled} onClick={() => send(edit.value)}>
          {edit.label}
        </button>
      </div>
    </section>
  );
}
