import { useId, useState } from 'react';
import type { EstimateResult, EstimateTableBlock } from '../../api/types';
import { formatCoefficient, formatDateVi, formatM2, viNumber } from '../../utils/format';
import { BUILDING_TYPE_VI, NO_RATE_DISCLAIMER, WORK_SCOPE_VI } from '../../utils/labels';
import { EvidenceCell } from '../common/EvidenceCell';
import { StatusBadge } from '../common/StatusBadge';

/** Ghi chú miễn trừ mục 6.6 — dùng khi backend không gửi `disclaimer` (không bao giờ để trống). */
export function fallbackDisclaimer(d: EstimateResult): string {
  // Không có đơn giá / chi phí: không nói "dựa trên đơn giá tham chiếu" (cùng câu với backend explain.build_disclaimer).
  if (!d.rate || d.rate.status === 'unavailable' || d.cost_min == null) return NO_RATE_DISCLAIMER;
  const src = d.rate?.source;
  const date = formatDateVi(src?.updated_at);
  const ref = src ? ` (${src.label}${date ? `, cập nhật ${date}` : ''})` : '';
  return `Đây là khái toán sơ bộ dựa trên đơn giá tham chiếu${ref} và các giả định nêu trên, chưa tính điều kiện thực tế của khu đất. Không phải báo giá thi công. Vui lòng yêu cầu báo giá từ nhà thầu để có con số chính xác.`;
}

function floorsText(d: EstimateResult): string {
  const inp = d.input;
  const extra: string[] = [];
  if (inp.has_mezzanine) extra.push('có tầng lửng');
  if (inp.basement) extra.push('có tầng hầm');
  return extra.length ? `${inp.floors} (${extra.join(', ')})` : String(inp.floors);
}

function landText(d: EstimateResult): string {
  const inp = d.input;
  const area = formatM2(inp.land_area_m2 ?? null);
  if (inp.land_width_m && inp.land_length_m) {
    return `${area} (${viNumber(inp.land_width_m)} × ${viNumber(inp.land_length_m)} m)`;
  }
  return area;
}

function List({ title, items, tone }: { title: string; items?: string[]; tone?: 'warn' }) {
  if (!items || items.length === 0) return null;
  return (
    <div className={`dz-note${tone === 'warn' ? ' dz-note--warn' : ''}`}>
      <strong>{title}</strong>
      <ul>
        {items.map((t, i) => (
          <li key={i}>{t}</li>
        ))}
      </ul>
    </div>
  );
}

export function EstimateTable({ block }: { block: EstimateTableBlock }) {
  const d = block.data;
  const [open, setOpen] = useState(false);
  const detailId = useId();
  const disclaimer = d.disclaimer && d.disclaimer.trim() ? d.disclaimer : fallbackDisclaimer(d);
  const scope = WORK_SCOPE_VI[d.input.work_scope] ?? d.input.work_scope;
  const place = [d.input.district, d.input.province].filter(Boolean).join(', ');

  return (
    <section className="dz-card dz-estimate" aria-label="Bảng khái toán chi phí">
      <h3 className="dz-card__title">Khái toán chi phí sơ bộ</h3>
      <p className="dz-card__sub">
        {BUILDING_TYPE_VI[d.input.building_type] ?? d.input.building_type}
        {place ? ` · ${place}` : ''}
      </p>
      <table className="dz-table dz-table--kv">
        <tbody>
          <tr>
            <th scope="row">Diện tích đất</th>
            <td>{landText(d)}</td>
          </tr>
          <tr>
            <th scope="row">Số tầng</th>
            <td>{floorsText(d)}</td>
          </tr>
          <tr>
            <th scope="row">Diện tích sàn quy đổi</th>
            <td>
              <span className="dz-inline">
                <span>{formatM2(d.converted_area_m2)}</span>
                <StatusBadge status={d.rules_status} title={`Hệ số khái toán (phiên bản ${d.rules_version})`} />
              </span>
              <button
                type="button"
                className="dz-linkbtn"
                aria-expanded={open}
                aria-controls={detailId}
                onClick={() => setOpen((v) => !v)}
              >
                {open ? 'Ẩn chi tiết hệ số' : 'Xem chi tiết hệ số'}
              </button>
            </td>
          </tr>
          <tr>
            <th scope="row">Phạm vi</th>
            <td>{scope}</td>
          </tr>
          <tr>
            <th scope="row">Đơn giá tham chiếu</th>
            <td>
              <EvidenceCell ev={d.rate} showSourceLine title="Đơn giá tham chiếu" />
            </td>
          </tr>
          <tr className="dz-row-em">
            <th scope="row">Chi phí sơ bộ</th>
            <td>
              <EvidenceCell ev={d.cost} emphasize title="Chi phí sơ bộ" />
            </td>
          </tr>
        </tbody>
      </table>

      {open && (
        <div id={detailId} className="dz-table-scroll" role="region" aria-label="Chi tiết hệ số khái toán" tabIndex={0}>
          <table className="dz-table dz-table--grid">
            <thead>
              <tr>
                <th scope="col">Hạng mục</th>
                <th scope="col" className="dz-num">
                  A<sub>i</sub> (m²)
                </th>
                <th scope="col" className="dz-num">
                  k<sub>i</sub>
                </th>
                <th scope="col" className="dz-num">
                  A<sub>i</sub>×k<sub>i</sub>
                </th>
              </tr>
            </thead>
            <tbody>
              {d.items.map((it, i) => (
                <tr key={`${it.rule_code}-${i}`}>
                  <td>{it.label_vi}</td>
                  <td className="dz-num">{viNumber(it.area_m2)}</td>
                  <td className="dz-num">{formatCoefficient(it.coefficient)}</td>
                  <td className="dz-num">{viNumber(it.converted_m2)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th scope="row" colSpan={3}>
                  Tổng diện tích quy đổi
                </th>
                <td className="dz-num">
                  <strong>{formatM2(d.converted_area_m2)}</strong>
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}

      <List title="Lưu ý" items={d.warnings} tone="warn" />
      <List title="Giả định" items={d.assumptions} />
      <List title="Chưa bao gồm" items={d.exclusions} />
      <List title="Yếu tố có thể làm tăng chi phí" items={d.cost_drivers} />

      <p className="dz-disclaimer" role="note">
        <strong>Lưu ý quan trọng: </strong>
        {disclaimer}
      </p>
    </section>
  );
}
