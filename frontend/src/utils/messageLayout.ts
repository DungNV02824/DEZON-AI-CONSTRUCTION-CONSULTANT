import type { UIBlock } from '../api/types';

/** Một phần của tin nhắn trợ lý theo thứ tự hiển thị: đoạn chữ hoặc block. */
export type MessagePart = { kind: 'text'; text: string } | { kind: 'block'; block: UIBlock; index: number };

/** Đoạn chữ là câu hỏi chờ khách trả lời bằng nút (vd câu hỏi ngân sách sau khái toán — BUDGET_TAIL của backend). */
const isQuestion = (p: string) => /\?\s*$/.test(p) || /cho em biết/i.test(p);
/** Đoạn giới thiệu nhà thầu đề xuất (đứng ngay trên danh sách nhà thầu). */
const isContractorIntro = (p: string) => /nhà thầu/i.test(p) && /đề xuất|giới thiệu/i.test(p);

/**
 * Sắp xếp tin nhắn trợ lý cho hợp lí: mỗi đoạn chữ đứng ngay trên block mà nó nói tới, thay vì toàn bộ chữ ở đầu và
 * mọi block dồn phía dưới.
 *  - Đoạn giới thiệu nhà thầu (không phải đoạn đầu) -> ngay trên `contractor_list` (khi trước đó còn block khác,
 *    vd bảng khái toán).
 *  - Đoạn cuối là câu hỏi (vd "Anh/chị cho em biết ngân sách dự kiến ...") -> ngay trên `quick_replies` cuối tin
 *    nhắn (khi trước đó còn block khác), để câu hỏi và nút trả lời liền nhau.
 * Không tách được -> giữ thứ tự cũ (chữ trước, block sau).
 */
export function layoutMessage(content: string, blocks: UIBlock[]): MessagePart[] {
  const parts: MessagePart[] = [];
  const blockParts = (from: number, to: number) => {
    for (let i = from; i < to; i++) parts.push({ kind: 'block', block: blocks[i]!, index: i });
  };
  const pushText = (paras: string[]) => {
    if (paras.length) parts.push({ kind: 'text', text: paras.join('\n\n') });
  };

  let paras = content
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
  if (!paras.length || !blocks.length) {
    if (content.trim()) parts.push({ kind: 'text', text: content });
    blockParts(0, blocks.length);
    return parts;
  }

  // Gợi ý câu hỏi tiếp theo (suggestions) luôn ở cuối tin nhắn; phần sắp xếp bên dưới xét các block trước nó.
  let end = blocks.length;
  while (end > 0 && blocks[end - 1]!.type === 'suggestions') end--;
  if (end === 0) {
    pushText(paras);
    blockParts(0, blocks.length);
    return parts;
  }

  // Câu hỏi cuối tin nhắn -> ngay trên nút trả lời (quick_replies là block cuối và không phải block duy nhất).
  const last = end - 1;
  let question: string | null = null;
  if (blocks[last]!.type === 'quick_replies' && last > 0 && isQuestion(paras[paras.length - 1]!)) {
    question = paras[paras.length - 1]!;
    paras = paras.slice(0, -1);
  }
  const qrStart = question !== null ? last : end;

  // Đoạn giới thiệu nhà thầu -> ngay trên danh sách nhà thầu (khi trước danh sách còn block khác).
  const ci = blocks.findIndex((b) => b.type === 'contractor_list');
  const k = ci > 0 && ci < qrStart ? paras.findIndex((p, i) => i > 0 && isContractorIntro(p)) : -1;
  if (k > 0) {
    pushText(paras.slice(0, k));
    blockParts(0, ci);
    pushText(paras.slice(k));
    blockParts(ci, qrStart);
  } else {
    pushText(paras);
    blockParts(0, qrStart);
  }
  if (question !== null) {
    pushText([question]);
    blockParts(qrStart, end);
  }
  blockParts(end, blocks.length);
  return parts;
}
