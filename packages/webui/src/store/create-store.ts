/** Framework-independent store. Reducers must preserve references for unchanged data. */
export interface ReadableStore<State> {
  getState: () => State;
  getInitialState: () => State;
  subscribe: (listener: () => void) => () => void;
}
export interface Store<State, Action> extends ReadableStore<State> {
  dispatch: (action: Action) => void;
  batch: (run: () => void) => void;
}
export function createStore<State, Action>(
  initial: State,
  reducer: (state: State, action: Action) => State,
): Store<State, Action> {
  let state = initial;
  const listeners = new Set<() => void>();
  let depth = 0;
  let pending = false;
  const publish = () => {
    if (depth) { pending = true; return; }
    [...listeners].forEach(listener => listener());
  };
  return {
    getState: () => state,
    getInitialState: () => initial,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    batch: run => {
      depth++;
      try { run(); } finally { depth--; if (!depth && pending) { pending = false; publish(); } }
    },
    dispatch: (action) => {
      const next = reducer(state, action);
      if (Object.is(next, state)) return;
      state = next;
      publish();
    },
  };
}
export type Equality<Value> = (previous: Value, next: Value) => boolean;
/** Cached selection shared by the React adapter and subscription tests. */
export function selectStore<State, Value>(
  store: ReadableStore<State>,
  selector: (state: State) => Value,
  equal: Equality<Value> = Object.is,
): ReadableStore<Value> {
  let snapshot = store.getState();
  let selection = selector(snapshot);
  const initial = Object.is(snapshot, store.getInitialState())
    ? selection
    : selector(store.getInitialState());
  const getState = () => {
    const next = store.getState();
    if (!Object.is(snapshot, next)) {
      const value = selector(next);
      if (!equal(selection, value)) selection = value;
      snapshot = next;
    }
    return selection;
  };
  return {
    getState,
    getInitialState: () => initial,
    subscribe: (listener) => {
      let previous = getState();
      return store.subscribe(() => {
        const next = getState();
        if (Object.is(previous, next)) return;
        previous = next;
        listener();
      });
    },
  };
}
/** For small records/arrays selected from immutable state. */
export function shallowEqual<Value>(previous: Value, next: Value): boolean {
  if (Object.is(previous, next)) return true;
  if (!previous || !next || typeof previous !== 'object' || typeof next !== 'object') return false;
  const keys = Object.keys(previous);
  return (
    keys.length === Object.keys(next).length &&
    keys.every(
      (key) =>
        Object.hasOwn(next, key) && Object.is(Reflect.get(previous, key), Reflect.get(next, key)),
    )
  );
}
