'use client';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import copy from '../../content/fr.json';
import { IconButton } from './button';
import { Icon } from './icon';
import { createUiClient, type UiClient } from '../../store/client/client';
import { useStoreSelector } from '../providers/use-selector';
import type { UiNotice } from '../../store/ui-state';
export type NotificationKind = 'info' | 'success' | 'warning' | 'error';
type Notice = UiNotice;
const labels = {
  info: copy.common.info,
  success: copy.common.success,
  warning: copy.common.warning,
  error: copy.common.error,
};
const Context = createContext<{
  notify: (text: string, kind?: NotificationKind) => void;
  registerModal: (element: HTMLDialogElement) => () => void;
} | null>(null);
export function useNotifications() {
  const value = useContext(Context);
  if (!value) throw new Error('NotificationProvider is required.');
  return value;
}
export function useModalNotifications() {
  return useContext(Context)?.registerModal;
}
export function NotificationProvider({
  children,
  client: supplied,
}: {
  children: ReactNode;
  client?: UiClient;
}) {
  const [owned] = useState(() => supplied ?? createUiClient());
  const client = supplied ?? owned;
  const notices = useStoreSelector(client.store, (state) => state.notifications);
  const [modals, setModals] = useState<HTMLDialogElement[]>([]);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const notify = useCallback(
    (message: string, kind: NotificationKind = 'success') =>
      client.notifications.show(message, kind),
    [client],
  );
  const registerModal = useCallback((element: HTMLDialogElement) => {
    setModals((items) => [...items.filter((item) => item !== element), element]);
    return () => setModals((items) => items.filter((item) => item !== element));
  }, []);
  const dismiss = useCallback((id: string) => client.notifications.dismiss(id), [client]);
  const context = useMemo(() => ({ notify, registerModal }), [notify, registerModal]);
  return (
    <Context.Provider value={context}>
      {children}
      {mounted &&
        createPortal(
          <section className="notifications" aria-label={copy.common.notifications}>
            <div className="notification-stack">
              {notices.map((item) => (
                <Toast key={item.id} notice={item} dismiss={dismiss} />
              ))}
            </div>
          </section>,
          modals.at(-1) ?? document.body,
        )}
    </Context.Provider>
  );
}
function Toast({ notice, dismiss }: { notice: Notice; dismiss: (id: string) => void }) {
  const [paused, setPaused] = useState(false);
  const remaining = useRef(notice.duration);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined,
      started = 0;
    const stop = () => {
      if (timer !== undefined) {
        clearTimeout(timer);
        remaining.current = Math.max(1, remaining.current - (performance.now() - started));
        timer = undefined;
      }
    };
    const schedule = () => {
      stop();
      if (remaining.current && !paused && !document.hidden) {
        started = performance.now();
        timer = setTimeout(() => dismiss(notice.id), remaining.current);
      }
    };
    schedule();
    document.addEventListener('visibilitychange', schedule);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', schedule);
    };
  }, [paused, dismiss, notice.id]);
  return (
    <div
      className={`notification notification--${notice.kind}`}
      onPointerEnter={(event) => {
        if (event.pointerType === 'mouse') setPaused(true);
      }}
      onPointerLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setPaused(false);
      }}
    >
      <span className="notification-symbol">
        <Icon
          name={notice.kind === 'success' ? 'check' : notice.kind === 'info' ? 'info' : 'alert'}
        />
      </span>
      <div
        className="notification-content"
        role={notice.kind === 'error' ? 'alert' : 'status'}
        aria-atomic="true"
      >
        <strong>{labels[notice.kind]}</strong>
        <p>{notice.message}</p>
      </div>
      <IconButton
        icon="close"
        label={copy.notifications.fermerLaNotification}
        onClick={() => dismiss(notice.id)}
      />
    </div>
  );
}
