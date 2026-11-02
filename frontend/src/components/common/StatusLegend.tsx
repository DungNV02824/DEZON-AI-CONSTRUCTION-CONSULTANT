import { STATUS_LABEL_VI, VERIFICATION_STATUSES } from '../../utils/labels';

/** Chú thích màu trạng thái (dùng dưới bảng so sánh có badge dạng chấm). */
export function StatusLegend() {
  return (
    <ul className="dz-legend" aria-label="Chú thích trạng thái dữ liệu">
      {VERIFICATION_STATUSES.map((s) => (
        <li key={s}>
          <span className={`dz-dot dz-dot--${s}`} aria-hidden="true" />
          {STATUS_LABEL_VI[s]}
        </li>
      ))}
    </ul>
  );
}
