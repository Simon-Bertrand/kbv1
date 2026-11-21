/** Pure store entry: safe for server imports; create one instance per request/provider. */
export {
  createStore,
  selectStore,
  shallowEqual,
  type Store,
  type ReadableStore,
  type Equality,
} from './create-store';
export { createUiState, createUiStore, uiReducer, type UiState, type UiAction } from './ui-state';
export { chatReducer, activeConversation, type ChatState, type ChatAction } from './chat/state';
