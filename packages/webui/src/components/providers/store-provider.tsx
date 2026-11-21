'use client';
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { createUiStore, type UiState, type UiStore } from '../../store/ui-state';
import { useStoreSelector } from './use-selector';
import type { Equality } from '../../store/create-store';
const Context = createContext<UiStore | null>(null);
export function UiProvider({
  children,
  initialState,
  store: suppliedStore,
  restorePreferences = true,
}: {
  children: ReactNode;
  initialState?: UiState;
  store?: UiStore;
  restorePreferences?: boolean;
}) {
  const [ownedStore] = useState(() => suppliedStore ?? createUiStore(initialState));
  const store = suppliedStore ?? ownedStore;
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    if (restorePreferences) {
      let preference: UiState['themePreference'] = 'system';
      try {
        const theme = localStorage.getItem('reno-plus-theme');
        if (theme === 'light' || theme === 'dark') preference = theme;
        const width = Number(localStorage.getItem('reno-plus-sidebar-width'));
        store.dispatch({
          type: 'sidebar/restored',
          width: Number.isFinite(width) && width >= 200 && width <= 360 ? width : 224,
          collapsed: localStorage.getItem('reno-plus-sidebar-collapsed') === 'true',
        });
      } catch {}
      store.dispatch({
        type: 'theme/restored',
        preference,
        theme: preference === 'system' ? (media.matches ? 'dark' : 'light') : preference,
      });
    }
    document.documentElement.dataset.theme = store.getState().theme;
    let previous = store.getState();
    const unsubscribe = store.subscribe(() => {
      const next = store.getState();
      if (next.theme !== previous.theme) document.documentElement.dataset.theme = next.theme;
      try {
        if (!restorePreferences) {
          previous = next;
          return;
        }
        if (next.sidebar !== previous.sidebar) {
          localStorage.setItem('reno-plus-sidebar-width', String(next.sidebar.width));
          localStorage.setItem('reno-plus-sidebar-collapsed', String(next.sidebar.collapsed));
        }
        if (next.themePreference !== previous.themePreference && next.themePreference !== 'system')
          localStorage.setItem('reno-plus-theme', next.themePreference);
      } catch {}
      previous = next;
    });
    const system = () =>
      store.dispatch({ type: 'theme/system', theme: media.matches ? 'dark' : 'light' });
    media.addEventListener('change', system);
    return () => {
      unsubscribe();
      media.removeEventListener('change', system);
    };
  }, [store, restorePreferences]);
  return <Context.Provider value={store}>{children}</Context.Provider>;
}
export function useUiStore(): UiStore {
  const store = useContext(Context);
  if (!store) throw new Error('UiProvider is required.');
  return store;
}
export function useUiSelector<Value>(selector: (state: UiState) => Value, equal?: Equality<Value>) {
  return useStoreSelector(useUiStore(), selector, equal);
}
export function useUiDispatch() {
  return useUiStore().dispatch;
}
