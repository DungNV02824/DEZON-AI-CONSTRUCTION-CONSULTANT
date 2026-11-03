import type { UIBlock } from '../../api/types';
import { BudgetOptions } from './BudgetOptions';
import { ComparisonTable } from './ComparisonTable';
import { ContractorDetail } from './ContractorDetail';
import { ContractorList } from './ContractorList';
import { EstimateTable } from './EstimateTable';
import { QuestionStep } from './QuestionStep';
import { QuickReplies } from './QuickReplies';
import { QuoteRequestForm } from './QuoteRequestForm';
import { Suggestions } from './Suggestions';
import { SummaryConfirm } from './SummaryConfirm';

/**
 * Render một UI block theo `type` (mục 8.1).
 * `active` = block thuộc tin nhắn trợ lý mới nhất -> các nút gửi tin nhắn (quick replies, xác nhận) còn hiệu lực.
 * Kiểu block lạ (backend mới hơn frontend) -> bỏ qua thay vì lỗi.
 */
export function BlockRenderer({ block, active }: { block: UIBlock; active: boolean }) {
  switch (block.type) {
    case 'quick_replies':
      return <QuickReplies block={block} active={active} />;
    case 'question_step':
      return <QuestionStep block={block} active={active} />;
    case 'summary_confirm':
      return <SummaryConfirm block={block} active={active} />;
    case 'estimate_table':
      return <EstimateTable block={block} />;
    case 'budget_options':
      return <BudgetOptions block={block} active={active} />;
    case 'contractor_list':
      return <ContractorList block={block} />;
    case 'comparison_table':
      return <ComparisonTable block={block} />;
    case 'contractor_detail':
      return <ContractorDetail block={block} />;
    case 'source_badges':
      return null; // chủ sản phẩm yêu cầu bỏ dòng "Nguồn:"
    case 'quote_request_form':
      return <QuoteRequestForm block={block} />;
    case 'suggestions':
      return <Suggestions block={block} active={active} />;
    default:
      return null;
  }
}
