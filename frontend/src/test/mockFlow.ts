import type { QuestionStepBlock, UIBlock } from '../api/types';

/** Thẻ question_step trong câu trả lời (undefined nếu không có). */
export const questionOf = (r: { blocks: UIBlock[] }) =>
  r.blocks.find((b): b is QuestionStepBlock => b.type === 'question_step');
