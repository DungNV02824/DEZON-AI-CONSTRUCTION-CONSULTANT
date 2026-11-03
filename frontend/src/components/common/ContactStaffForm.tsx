import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import type { ContactSource, QuoteFormContractor } from '../../api/types';
import { useWidget } from '../../state/context';
import { formatVnPhone, isValidVnPhone, toNationalPhone } from '../../utils/format';

/** localStorage: họ tên + SĐT lần gửi liên hệ thành công gần nhất (điền sẵn cho lần sau). */
export const CONTACT_INFO_KEY = 'dezon_contact_info';

const NAME_MAX = 200;
const NOTE_MAX = 1000;

/** Kết quả gửi thành công: mã yêu cầu + SĐT khách đã để lại (để nhắc lại trong thông báo). */
export interface ContactDone {
  code: string;
  phone: string;
}

interface SavedInfo {
  name: string;
  phone: string;
}

function readSaved(): SavedInfo {
  try {
    const raw = typeof window !== 'undefined' ? window.localStorage.getItem(CONTACT_INFO_KEY) : null;
    if (!raw) return { name: '', phone: '' };
    const v: unknown = JSON.parse(raw);
    if (!v || typeof v !== 'object') return { name: '', phone: '' };
    const o = v as Record<string, unknown>;
    return { name: typeof o.name === 'string' ? o.name : '', phone: typeof o.phone === 'string' ? o.phone : '' };
  } catch {
    return { name: '', phone: '' };
  }
}

function writeSaved(info: SavedInfo) {
  try {
    window.localStorage.setItem(CONTACT_INFO_KEY, JSON.stringify(info));
  } catch {
    /* localStorage bị chặn (chế độ riêng tư) — không điền sẵn lần sau */
  }
}

export function contactSuccessText(phone: string): string {
  return `Đã thông báo cho nhân viên Dezon. Nhân viên sẽ liên hệ anh/chị qua số ${formatVnPhone(phone)} trong thời gian sớm nhất.`;
}

export const CONTACT_SUBMIT_ERROR = 'Chưa gửi được yêu cầu liên hệ. Anh/chị vui lòng thử lại sau ít phút.';

interface Errors {
  name?: string;
  phone?: string;
  note?: string;
  submit?: string;
}

export interface ContactStaffFormProps {
  source: ContactSource;
  /** Nhà thầu khách đang xem (thẻ / hồ sơ) — gửi kèm contractor_id, tiêu đề nhắc tên nhà thầu. */
  contractor?: QuoteFormContractor | null;
  /** Đã gửi trước đó (form đóng rồi mở lại) -> hiện ngay thông báo thành công. */
  result?: ContactDone | null;
  onSubmitted?: (done: ContactDone) => void;
  /** Có giá trị -> thông báo thành công kèm nút "Gửi yêu cầu khác" (về lại form trống). */
  onAgain?: () => void;
  /** Form vừa mở -> đưa con trỏ vào ô Họ tên. */
  focusOnMount?: boolean;
  /** card: khung thẻ riêng (dưới thẻ nhà thầu); plain: không viền (trong popover). */
  variant?: 'card' | 'plain';
}

/**
 * Biểu mẫu "Liên hệ nhân viên": khách để lại họ tên + SĐT, nhân viên Dezon gọi lại (POST /api/contact-requests).
 * Dùng ở popover dưới ô nhập và ngay dưới thẻ / hồ sơ nhà thầu.
 */
export function ContactStaffForm({
  source,
  contractor = null,
  result = null,
  onSubmitted,
  onAgain,
  focusOnMount = false,
  variant = 'card',
}: ContactStaffFormProps) {
  const { submitContact, chatSessionId } = useWidget();
  const uid = useId();
  const [saved] = useState(readSaved);
  const [name, setName] = useState(saved.name);
  const [phone, setPhone] = useState(saved.phone);
  const [note, setNote] = useState('');
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState<ContactDone | null>(result);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setDone(result);
  }, [result]);
  // Mở form (hoặc quay lại form sau "Gửi yêu cầu khác") -> con trỏ vào ô Họ tên.
  useEffect(() => {
    if (!done && focusOnMount) nameRef.current?.focus();
  }, [done, focusOnMount]);
  // Gửi xong: form (và nút đang focus) bị thay bằng thẻ thành công -> chuyển focus vào thẻ để trình đọc màn hình đọc
  // nội dung + mã, và phím Esc của khung bật lên vẫn nhận được (focus không rơi ra ngoài shadow root).
  const doneRef = useRef<HTMLDivElement>(null);
  const justSubmitted = useRef(false);
  useEffect(() => {
    if (done && justSubmitted.current) {
      justSubmitted.current = false;
      doneRef.current?.focus();
    }
  }, [done]);

  const validate = (): Errors => {
    const e: Errors = {};
    if (!name.trim()) e.name = 'Vui lòng nhập họ tên.';
    else if (name.trim().length > NAME_MAX) e.name = `Họ tên tối đa ${NAME_MAX} ký tự.`;
    if (!phone.trim()) e.phone = 'Vui lòng nhập số điện thoại.';
    else if (!isValidVnPhone(phone)) e.phone = 'Số điện thoại chưa đúng định dạng Việt Nam (vd 0901 234 567).';
    if (note.trim().length > NOTE_MAX) e.note = `Ghi chú tối đa ${NOTE_MAX} ký tự.`;
    return e;
  };

  const onSubmit = async (ev: FormEvent) => {
    ev.preventDefault();
    if (submitting) return;
    const e = validate();
    setErrors(e);
    if (Object.keys(e).length) {
      if (e.name) nameRef.current?.focus();
      return;
    }
    const nationalPhone = toNationalPhone(phone);
    setSubmitting(true);
    try {
      const res = await submitContact({
        customer_name: name.trim(),
        customer_phone: nationalPhone,
        note: note.trim() || null,
        chat_session_id: chatSessionId ?? null,
        contractor_id: contractor?.id ?? null,
        source,
      });
      writeSaved({ name: name.trim(), phone: nationalPhone });
      const d = { code: res.code, phone: nationalPhone };
      justSubmitted.current = true;
      setDone(d);
      setNote('');
      onSubmitted?.(d);
    } catch {
      setErrors({ submit: CONTACT_SUBMIT_ERROR });
    } finally {
      setSubmitting(false);
    }
  };

  const cls = `dz-contactform${variant === 'card' ? ' dz-card' : ''}`;

  if (done) {
    return (
      <div ref={doneRef} tabIndex={-1} className={`${cls} dz-contactform--done`} role="status" aria-live="polite">
        <p className="dz-contactform__ok">{contactSuccessText(done.phone)}</p>
        <p className="dz-small">
          Mã yêu cầu: <strong className="dz-code">{done.code}</strong>
        </p>
        {onAgain && (
          <button
            type="button"
            className="dz-linkbtn dz-small"
            onClick={() => {
              setDone(null);
              onAgain();
            }}
          >
            Gửi yêu cầu khác
          </button>
        )}
      </div>
    );
  }

  const field = (id: string) => `${uid}-${id}`;
  return (
    <form className={`${cls} dz-form`} onSubmit={onSubmit} noValidate aria-labelledby={field('title')}>
      <div>
        <h3 id={field('title')} className="dz-card__title dz-contactform__title">
          {contractor ? `Nhân viên Dezon sẽ liên hệ anh/chị về ${contractor.name}` : 'Liên hệ nhân viên Dezon'}
        </h3>
        <p className="dz-muted dz-small">Anh/chị để lại họ tên và số điện thoại, nhân viên Dezon sẽ gọi lại tư vấn.</p>
      </div>
      <div className="dz-field">
        <label htmlFor={field('name')}>
          Họ tên <span aria-hidden="true">*</span>
        </label>
        <input
          ref={nameRef}
          id={field('name')}
          type="text"
          autoComplete="name"
          maxLength={NAME_MAX}
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-invalid={Boolean(errors.name)}
          aria-describedby={errors.name ? field('name-err') : undefined}
          required
        />
        {errors.name && (
          <p id={field('name-err')} className="dz-error">
            {errors.name}
          </p>
        )}
      </div>
      <div className="dz-field">
        <label htmlFor={field('phone')}>
          Số điện thoại <span aria-hidden="true">*</span>
        </label>
        <input
          id={field('phone')}
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          placeholder="0901 234 567"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          aria-invalid={Boolean(errors.phone)}
          aria-describedby={errors.phone ? field('phone-err') : undefined}
          required
        />
        {errors.phone && (
          <p id={field('phone-err')} className="dz-error">
            {errors.phone}
          </p>
        )}
      </div>
      <div className="dz-field">
        <label htmlFor={field('note')}>Ghi chú (không bắt buộc)</label>
        <textarea
          id={field('note')}
          rows={2}
          maxLength={NOTE_MAX}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          aria-invalid={Boolean(errors.note)}
          aria-describedby={errors.note ? field('note-err') : undefined}
        />
        {errors.note && (
          <p id={field('note-err')} className="dz-error">
            {errors.note}
          </p>
        )}
      </div>
      {errors.submit && (
        <p className="dz-error" role="alert">
          {errors.submit}
        </p>
      )}
      <button type="submit" className="dz-btn dz-btn--primary dz-btn--block" disabled={submitting}>
        {submitting ? 'Đang gửi…' : 'Gửi yêu cầu liên hệ'}
      </button>
    </form>
  );
}
