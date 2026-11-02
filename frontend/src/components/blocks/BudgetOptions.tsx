import type { BudgetOptionsBlock } from '../../api/types';
import { useWidget } from '../../state/context';
import { formatM2, formatVnd } from '../../utils/format';
import { EvidenceCell } from '../common/EvidenceCell';

export function BudgetOptions({ block, active }: { block: BudgetOptionsBlock; active: boolean }) {
  const { send, busy } = useWidget();
  const d = block.data;
  return (
    <section className="dz-card" aria-label="Phương án điều chỉnh ngân sách">
      <h3 className="dz-card__title">Phương án điều chỉnh ngân sách</h3>
      <dl className="dz-kv">
        <div className="dz-kv__row">
          <dt>Ngân sách của anh/chị</dt>
          <dd>
            <EvidenceCell ev={d.target_budget} badge="none" />
          </dd>
        </div>
        <div className="dz-kv__row">
          <dt>Khái toán hiện tại</dt>
          <dd>
            <EvidenceCell ev={d.current_cost} title="Khái toán hiện tại" />
          </dd>
        </div>
      </dl>
      {d.options.length === 0 && <p className="dz-muted">Chưa có phương án điều chỉnh phù hợp.</p>}
      <ul className="dz-options">
        {d.options.map((o) => (
          <li key={o.code} className={`dz-option${o.fits_budget ? ' dz-option--fit' : ''}`}>
            <div className="dz-option__head">
              <span className="dz-option__label">{o.label_vi}</span>
              {o.fits_budget && <span className="dz-pill dz-pill--ok">Vừa ngân sách</span>}
            </div>
            <p className="dz-option__desc">{o.description}</p>
            <div className="dz-option__cost">
              <span className="dz-muted">Chi phí dự kiến:</span> <EvidenceCell ev={o.cost} emphasize title={o.label_vi} />
            </div>
            <div className="dz-option__meta">
              <span>Diện tích quy đổi {formatM2(o.converted_area_m2)}</span>
              {typeof o.saving_min === 'number' && o.saving_min > 0 && (
                <span className="dz-saving">Tiết kiệm khoảng {formatVnd(o.saving_min)} so với khái toán hiện tại</span>
              )}
            </div>
            {active && (
              <button
                type="button"
                className="dz-btn dz-btn--sm"
                disabled={busy}
                onClick={() => send(`Tôi chọn phương án: ${o.label_vi}`)}
              >
                Chọn phương án này
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
