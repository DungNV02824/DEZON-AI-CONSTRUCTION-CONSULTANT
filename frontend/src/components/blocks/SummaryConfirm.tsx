import type { SummaryConfirmBlock, SummaryItem } from '../../api/types';
import { useWidget } from '../../state/context';

const DEFAULT_CONFIRM = { label: 'Đúng rồi', value: 'Đúng rồi, tính khái toán giúp tôi' };
const DEFAULT_EDIT = { label: 'Sửa lại', value: 'Tôi muốn sửa lại thông tin' };
/** Thẻ gọn: chỉ hiện các mục chính (chủ sản phẩm: 4–5 dòng); mục còn lại gom vào "Xem thêm N mục". */
export const PRIMARY_SUMMARY_LABELS = ['Khu vực', 'Diện tích đất', 'Diện tích căn hộ', 'Số tầng', 'Phạm vi', 'Ngân sách'];

/**
 * Thẻ xác nhận thông tin trước khi khái toán: bảng nhãn | giá trị | "Sửa" (mỗi dòng gửi `edit_value`),
 * nút lớn xác nhận và nút "Cung cấp thêm thông tin..." (hỏi lần lượt các chi tiết đang tạm tính theo mặc định).
 * Payload cũ (không có các trường mới) -> tiêu đề "Thông tin công trình", nút "Đúng rồi" / "Sửa lại".
 */
export function SummaryConfirm({ block, active }: { block: SummaryConfirmBlock; active: boolean }) {
  const { send, busy } = useWidget();
  const confirm = block.confirm ?? DEFAULT_CONFIRM;
  const edit = block.edit ?? DEFAULT_EDIT;
  const confirmLabel = block.confirm_label || confirm.label;
  const confirmValue = block.confirm_value || confirm.value;
  const more = block.more_label && block.more_value ? { label: block.more_label, value: block.more_value } : null;
  const rowEdit = block.items.some((it) => !!it.edit_value);
  const isNew = !!(block.title || block.confirm_label || rowEdit || more);
  const disabled = !active || busy;
  const title = block.title || 'Thông tin công trình';
  // Payload mới: tiêu đề đã có loại công trình -> bảng chính chỉ giữ PRIMARY_SUMMARY_LABELS; payload cũ hiện đủ.
  const compact = isNew && block.items.some((it) => PRIMARY_SUMMARY_LABELS.includes(it.label));
  const primary = compact ? block.items.filter((it) => PRIMARY_SUMMARY_LABELS.includes(it.label)) : block.items;
  const extra = compact ? block.items.filter((it) => !PRIMARY_SUMMARY_LABELS.includes(it.label)) : [];
  const row = (it: SummaryItem, i: number) => (
    <tr key={`${it.label}-${i}`}>
      <th scope="row">{it.label}</th>
      <td>{it.value}</td>
      {rowEdit && (
        <td className="dz-sumtable__edit">
          {it.edit_value && (
            <button
              type="button"
              className="dz-sumtable__editbtn"
              aria-label={`Sửa ${it.label.toLowerCase()}`}
              disabled={disabled}
              onClick={() => send(it.edit_value!)}
            >
              Sửa
            </button>
          )}
        </td>
      )}
    </tr>
  );

  return (
    <section className={`dz-card dz-sumcard${isNew ? ' dz-sumcard--v2' : ''}`} aria-label={title}>
      <h3 className="dz-card__title">{title}</h3>
      <table className="dz-sumtable">
        <tbody>{primary.map(row)}</tbody>
      </table>
      {extra.length > 0 && (
        <details className="dz-sumcard__more">
          <summary>Xem thêm {extra.length} mục</summary>
          <table className="dz-sumtable">
            <tbody>{extra.map(row)}</tbody>
          </table>
        </details>
      )}
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
      {isNew ? (
        <div className="dz-sumcard__actions">
          <button
            type="button"
            className="dz-btn dz-btn--dark dz-btn--block dz-btn--lg"
            disabled={disabled}
            onClick={() => send(confirmValue)}
          >
            {confirmLabel}
          </button>
          {more && (
            <button type="button" className="dz-btn dz-btn--block" disabled={disabled} onClick={() => send(more.value)}>
              {more.label}
            </button>
          )}
          {!rowEdit && (
            <button type="button" className="dz-btn dz-btn--block dz-btn--ghost" disabled={disabled} onClick={() => send(edit.value)}>
              {edit.label}
            </button>
          )}
        </div>
      ) : (
        <div className="dz-actions">
          <button type="button" className="dz-btn dz-btn--primary" disabled={disabled} onClick={() => send(confirmValue)}>
            {confirmLabel}
          </button>
          <button type="button" className="dz-btn" disabled={disabled} onClick={() => send(edit.value)}>
            {edit.label}
          </button>
        </div>
      )}
    </section>
  );
}
