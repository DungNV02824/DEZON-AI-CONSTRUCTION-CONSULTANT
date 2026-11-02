import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';
import { SourcePopoverProvider } from '../components/common/SourcePopover';
import { WidgetContext, defaultActions, type WidgetActions } from '../state/context';

export function makeActions(overrides: Partial<WidgetActions> = {}): WidgetActions {
  return {
    ...defaultActions,
    send: vi.fn(),
    focusInput: vi.fn(),
    openContractor: vi.fn(),
    compare: vi.fn(),
    openQuoteForm: vi.fn(),
    recordClick: vi.fn(),
    submitQuote: vi.fn(async () => ({ ids: ['x'], code: 'DZ-TEST-0001' })),
    ...overrides,
  };
}

export function renderWithWidget(ui: ReactElement, overrides: Partial<WidgetActions> = {}) {
  const actions = makeActions(overrides);
  const utils = render(
    <WidgetContext.Provider value={actions}>
      <SourcePopoverProvider>{ui}</SourcePopoverProvider>
    </WidgetContext.Provider>,
  );
  return { ...utils, actions };
}
