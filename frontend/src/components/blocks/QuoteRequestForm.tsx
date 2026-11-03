import { useId, useState, type FormEvent } from 'react';
import type { QuoteRequestFormBlock } from '../../api/types';
import { useWidget } from '../../state/context';
import { isValidEmail, isValidVnPhone, toNationalPhone } from '../../utils/format';

/** Hợp đồng API: contractor_ids 1..5. */
const MAX_CONTRACTORS = 5;

interface Errors {
  name?: string;
  phone?: string;
  email?: string;
  contractors?: string;
  submit?: string;
}

export function QuoteRequestForm({ block }: { block: QuoteRequestFormBlock }) {
  const { submitQuote, quoteSubmitted, estimateSessionId, chatSessionId } = useWidget();
  const uid = useId();
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [note, setNote] = useState('');
  const [chosen, setChosen] = useState<number[]>(() => block.contractors.map((c) => c.id));
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [code, setCode] = useState<string | null>(block.submitted_code ?? null);
  const estimateId = block.estimate_session_id ?? estimateSessionId;

  const validate = (): Errors => {
    const e: Errors = {};
    if (!name.trim()) e.name = 'Vui lòng nhập họ tên.';
    if (!phone.trim()) e.phone = 'Vui lòng nhập số điện thoại.';
    else if (!isValidVnPhone(phone)) e.phone = 'Số điện thoại chưa đúng định dạng Việt Nam (vd 0901 234 567).';
    if (email.trim() && !isValidEmail(email)) e.email = 'Email chưa đúng định dạng.';
    if (chosen.length === 0) {
      e.contractors =
        block.contractors.length === 0
          ? 'Chưa có nhà thầu nào được chọn. Anh/chị vui lòng chọn nhà thầu trong danh sách đề xuất trước khi gửi yêu cầu.'
          : 'Vui lòng chọn ít nhất 1 nhà thầu.';
    } else if (chosen.length > MAX_CONTRACTORS) {
      e.contractors = `Mỗi yêu cầu gửi tối đa ${MAX_CONTRACTORS} nhà thầu.`;
    }
    return e;
  };

  const onSubmit = async (ev: FormEvent) => {
    ev.preventDefault();
    const e = validate();
    setErrors(e);
    if (Object.keys(e).length) return;
    setSubmitting(true);
    try {
      const res = await submitQuote({
        contractor_ids: chosen,
        estimate_session_id: estimateId ?? null,
        // Có cả khi chưa có khái toán: backend lấy phong cách / mức đầu tư của phiên để ghi kèm yêu cầu.
        chat_session_id: chatSessionId ?? null,
        customer_name: name.trim(),
        customer_phone: toNationalPhone(phone),
        customer_email: email.trim() || null,
        note: note.trim() || null,
      });
      setCode(res.code);
      quoteSubmitted(block, res.code);
    } catch {
      setErrors({ submit: 'Xin lỗi, em chưa gửi được yêu cầu. Anh/chị vui lòng thử lại sau ít phút.' });
    } finally {
      setSubmitting(false);
    }
  };

  if (code) {
    return (
      <section className="dz-card dz-success" aria-live="polite">
        <h3 className="dz-card__title">Đã gửi yêu cầu báo giá</h3>
        <p>
          Mã yêu cầu của anh/chị: <strong className="dz-code">{code}</strong>
        </p>
        <p className="dz-muted dz-small">Anh/chị vui lòng lưu lại mã này để tiện tra cứu khi nhà thầu liên hệ.</p>
      </section>
    );
  }

  const field = (id: string) => `${uid}-${id}`;
  return (
    <form className="dz-card dz-form" onSubmit={onSubmit} noValidate aria-label="Yêu cầu báo giá">
      <h3 className="dz-card__title">Yêu cầu báo giá</h3>
      {block.contractors.length > 0 && (
        <fieldset className="dz-fieldset">
          <legend>Gửi tới nhà thầu</legend>
          {block.contractors.map((c) => (
            <label key={c.id} className="dz-check">
              <input
                type="checkbox"
                checked={chosen.includes(c.id)}
                onChange={(e) =>
                  setChosen((prev) => (e.target.checked ? [...prev, c.id] : prev.filter((x) => x !== c.id)))
                }
              />
              {c.name}
            </label>
          ))}
          {errors.contractors && (
            <p className="dz-error" role="alert">
              {errors.contractors}
            </p>
          )}
        </fieldset>
      )}
      {block.contractors.length === 0 && errors.contractors && (
        <p className="dz-error" role="alert">
          {errors.contractors}
        </p>
      )}
      <div className="dz-field">
        <label htmlFor={field('name')}>
          Họ tên <span aria-hidden="true">*</span>
        </label>
        <input
          id={field('name')}
          type="text"
          autoComplete="name"
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
        <label htmlFor={field('email')}>Email (không bắt buộc)</label>
        <input
          id={field('email')}
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-invalid={Boolean(errors.email)}
          aria-describedby={errors.email ? field('email-err') : undefined}
        />
        {errors.email && (
          <p id={field('email-err')} className="dz-error">
            {errors.email}
          </p>
        )}
      </div>
      <div className="dz-field">
        <label htmlFor={field('note')}>Ghi chú</label>
        <textarea id={field('note')} rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      {estimateId && <p className="dz-muted dz-small">Yêu cầu sẽ đính kèm bản khái toán của anh/chị.</p>}
      {errors.submit && (
        <p className="dz-error" role="alert">
          {errors.submit}
        </p>
      )}
      <button type="submit" className="dz-btn dz-btn--primary dz-btn--block" disabled={submitting}>
        {submitting ? 'Đang gửi…' : 'Gửi yêu cầu báo giá'}
      </button>
    </form>
  );
}
