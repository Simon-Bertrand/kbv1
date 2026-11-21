'use client';
import { createContext, useContext, useEffect, useMemo, type ReactNode } from 'react';
import type { UiClient, UiClientState } from '../../store/client/client';
import { UiProvider } from './store-provider';
import { NotificationProvider } from '../ui/notifications';
import { Dialog } from '../ui/dialog';
import { useStoreSelector } from './use-selector';
import type { Equality } from '../../store/create-store';
import copy from '../../content/fr.json';
type Binding = { client: UiClient; store: UiClient['store'] };
const Context = createContext<Binding | null>(null);
/** React adapter for an externally owned, framework-independent UI controller. */
export function UiClientProvider({
  client,
  children,
  disposeOnUnmount = false,
  restorePreferences = false,
  dialogId,
}: {
  client: UiClient;
  children: ReactNode;
  disposeOnUnmount?: boolean;
  restorePreferences?: boolean;
  dialogId?: string;
}) {
  const binding = useMemo(() => {
    const initial = client.getState();
    return { client, store: { ...client.store, getInitialState: () => initial } };
  }, [client]);
  useEffect(
    () => () => {
      if (disposeOnUnmount) client.dispose();
    },
    [client, disposeOnUnmount],
  );
  return (
    <Context.Provider value={binding}>
      <UiProvider store={binding.store} restorePreferences={restorePreferences}>
        <NotificationProvider client={client}>
          {children}
          <ClientOverlays dialogId={dialogId} />
        </NotificationProvider>
      </UiProvider>
    </Context.Provider>
  );
}
export function useUiClient(): UiClient {
  const client = useContext(Context);
  if (!client) throw new Error('UiClientProvider is required.');
  return client.client;
}
export function useUiClientSelector<Value>(
  selector: (state: UiClientState) => Value,
  equal?: Equality<Value>,
) {
  const binding = useContext(Context);
  if (!binding) throw new Error('UiClientProvider is required.');
  return useStoreSelector(binding.store, selector, equal);
}
function ClientOverlays({ dialogId }: { dialogId?: string }) {
  const client = useUiClient();
  const modal = useUiClientSelector((state) => state.modal);
  return (
    <>
      <ClientAnnouncer />
      <Dialog
        id={dialogId}
        title={modal?.title ?? copy.common.info}
        open={!!modal}
        onOpenChange={(open) => {
          if (!open) client.modal.close();
        }}
      >
        <p className="dialog-text">{modal?.body}</p>
      </Dialog>
    </>
  );
}
function ClientAnnouncer() {
  const text = useUiClientSelector((state) => state.announcement);
  return (
    <div className="sr-only" id="announcer" role="status">
      {text}
    </div>
  );
}
