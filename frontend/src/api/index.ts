import type { WidgetConfig } from '../config';
import { createHttpClient, type ApiClient } from './client';
import { createMockClient } from './mock';

export function createApiClient(config: WidgetConfig): ApiClient {
  return config.mock ? createMockClient() : createHttpClient(config.apiBase);
}

export type { ApiClient, StreamHandlers } from './client';
