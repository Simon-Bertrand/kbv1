import { createStore } from './create-store';
import type { Source } from './chat/types';
import type { Annotation } from './feedback/types';
export interface UiNotice {
  readonly id: string;
  readonly message: string;
  readonly kind: 'info' | 'success' | 'warning' | 'error';
  readonly duration: number;
}
export interface UiState {
  readonly notifications: readonly UiNotice[];
  readonly sidebar: { readonly width: number; readonly collapsed: boolean };
  readonly theme: 'light' | 'dark';
  readonly themePreference: 'system' | 'light' | 'dark';
  readonly source: Source | null;
  readonly modal: { readonly title: string; readonly body: string } | null;
  readonly feedbackId: string | null;
  readonly feedbackError: string;
  readonly annotationsOpen: boolean;
  readonly announcement: string;
  readonly records: readonly Annotation[];
  readonly annotationsPersistent: boolean;
}
export type UiAction =
  | { type: 'notification/added'; notice: UiNotice }
  | { type: 'notification/dismissed'; id: string }
  | { type: 'sidebar/restored'; width: number; collapsed: boolean }
  | { type: 'sidebar/toggled' }
  | { type: 'sidebar/resized'; width: number }
  | { type: 'theme/restored'; theme: UiState['theme']; preference: UiState['themePreference'] }
  | { type: 'theme/selected'; theme: UiState['theme'] }
  | { type: 'theme/system'; theme: UiState['theme'] }
  | { type: 'source/opened'; source: Source | null }
  | { type: 'modal/opened'; modal: UiState['modal'] }
  | { type: 'feedback/opened'; id: string | null }
  | { type: 'feedback/failed'; error: string }
  | { type: 'annotations/opened'; open: boolean }
  | { type: 'annotations/replaced'; records: readonly Annotation[]; persistent: boolean }
  | { type: 'announcement/changed'; text: string };
export function createUiState(): UiState {
  return {
    notifications: [],
    sidebar: { width: 224, collapsed: false },
    theme: 'light',
    themePreference: 'system',
    source: null,
    modal: null,
    feedbackId: null,
    feedbackError: '',
    annotationsOpen: false,
    announcement: '',
    records: [],
    annotationsPersistent: true,
  };
}
export function uiReducer(state: UiState, action: UiAction): UiState {
  switch (action.type) {
    case 'notification/added':
      return {
        ...state,
        notifications: [
          ...state.notifications.filter(
            (notice) =>
              notice.message !== action.notice.message || notice.kind !== action.notice.kind,
          ),
          action.notice,
        ].slice(-3),
      };
    case 'notification/dismissed':
      return state.notifications.some((notice) => notice.id === action.id)
        ? {
            ...state,
            notifications: state.notifications.filter((notice) => notice.id !== action.id),
          }
        : state;
    case 'sidebar/restored':
      return state.sidebar.width === action.width && state.sidebar.collapsed === action.collapsed
        ? state
        : { ...state, sidebar: { width: action.width, collapsed: action.collapsed } };
    case 'sidebar/toggled':
      return { ...state, sidebar: { ...state.sidebar, collapsed: !state.sidebar.collapsed } };
    case 'sidebar/resized': {
      const width = Number.isFinite(action.width)
        ? Math.max(200, Math.min(360, Math.round(action.width)))
        : state.sidebar.width;
      return width === state.sidebar.width
        ? state
        : { ...state, sidebar: { ...state.sidebar, width } };
    }
    case 'theme/restored':
      return state.theme === action.theme && state.themePreference === action.preference
        ? state
        : { ...state, theme: action.theme, themePreference: action.preference };
    case 'theme/selected':
      return state.theme === action.theme && state.themePreference === action.theme
        ? state
        : { ...state, theme: action.theme, themePreference: action.theme };
    case 'theme/system':
      return state.themePreference !== 'system' || state.theme === action.theme
        ? state
        : { ...state, theme: action.theme };
    case 'modal/opened':
      return state.modal === action.modal ? state : { ...state, modal: action.modal };
    case 'source/opened':
      return state.source === action.source ? state : { ...state, source: action.source };
    case 'feedback/opened':
      return state.feedbackId === action.id && !state.feedbackError
        ? state
        : { ...state, feedbackId: action.id, feedbackError: '' };
    case 'feedback/failed':
      return state.feedbackError === action.error
        ? state
        : { ...state, feedbackError: action.error };
    case 'annotations/opened':
      return state.annotationsOpen === action.open
        ? state
        : { ...state, annotationsOpen: action.open };
    case 'annotations/replaced':
      return state.records === action.records && state.annotationsPersistent === action.persistent
        ? state
        : { ...state, records: action.records, annotationsPersistent: action.persistent };
    case 'announcement/changed':
      return state.announcement === action.text ? state : { ...state, announcement: action.text };
  }
}
export const createUiStore = (initial = createUiState()) => createStore(initial, uiReducer);
export type UiStore = ReturnType<typeof createUiStore>;
