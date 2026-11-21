'use client';
import { useMemo, useSyncExternalStore } from 'react';
import { selectStore, type Equality, type ReadableStore } from '../../store/create-store';
export function useStoreSelector<State, Value>(
  store: ReadableStore<State>,
  selector: (state: State) => Value,
  equal?: Equality<Value>,
): Value {
  const selection = useMemo(() => selectStore(store, selector, equal), [store, selector, equal]);
  return useSyncExternalStore(selection.subscribe, selection.getState, selection.getInitialState);
}
