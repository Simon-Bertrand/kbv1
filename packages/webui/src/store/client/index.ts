/** Shared TypeScript entry: protocol and client factory, without React/DOM side effects. */
export {
  createUiClient,
  type UiClient,
  type UiClientAdapter,
  type UiClientOptions,
  type UiClientState,
} from './client';
export {
  createUiCommands,
  parseUiCommands,
  UiClientError,
  type UiCommand,
  type UiCommandBatch,
  type NotificationKind,
} from './protocol';
export type { UiRequestOptions } from './http';

export { createUiState, type UiState, type UiNotice } from '../ui-state';
