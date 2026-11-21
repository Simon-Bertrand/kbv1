'use client';
/** React components: CSS and optional fonts are imported separately by the host. */
export { Button, IconButton } from './ui/button';
export { Icon, type IconName } from './ui/icon';
export { Dialog, type DialogProps } from './ui/dialog';
export { Markdown } from './ui/markdown';
export { NotificationProvider, useNotifications } from './ui/notifications';
export { ResponseComparison, type ResponseComparisonProps } from './chat/response-comparison';
export { SourceDialog } from './chat/source-dialog';
export { Message, type MessageProps } from './chat/message';
export {
  RenoProvider,
  useReno,
  useRenoActions,
  useChatSelector,
  useChatDispatch,
} from './chat/provider';
export { ChatWorkspace } from './chat/workspace';
export { useChat } from './chat/use-chat';
export { useTheme } from './layout/use-theme';

export {
  createStore,
  selectStore,
  shallowEqual,
  type Store,
  type ReadableStore,
  type Equality,
} from '../store/create-store';
export {
  createUiState,
  createUiStore,
  uiReducer,
  type UiState,
  type UiAction,
} from '../store/ui-state';
export {
  chatReducer,
  activeConversation,
  type ChatState,
  type ChatAction,
} from '../store/chat/state';

export {
  UiClientProvider,
  useUiClient,
  useUiClientSelector,
  UiProvider,
  useUiSelector,
  useUiDispatch,
  useStoreSelector,
} from './providers';
export type { UiClient, UiClientState } from '../store/client/client';
