'use client';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import type { ChatProvider, Conversation } from '../../store/chat/types';
import { ChatModel } from '../../store/chat/model';
import { DemoProvider } from '../../store/chat/demo-provider';

export function useChatInstance(
  initial: Conversation,
  announce: (text: string) => void,
  provider?: ChatProvider,
) {
  const announceRef = useRef(announce);
  announceRef.current = announce;
  const [chat] = useState(
    () =>
      new ChatModel(initial, provider ?? new DemoProvider(), (text) => announceRef.current(text)),
  );
  useEffect(() => {
    chat.connect();
    const save = () => chat.flush();
    const visibility = () => {
      if (document.visibilityState === 'hidden') save();
    };
    window.addEventListener('pagehide', save);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      window.removeEventListener('pagehide', save);
      document.removeEventListener('visibilitychange', visibility);
      chat.disconnect();
    };
  }, [chat]);
  return chat;
}

/** Compatibility hook subscribing to the entire chat. Prefer useChatSelector in composed UI. */
export function useChat(
  initial: Conversation,
  announce: (text: string) => void,
  provider?: ChatProvider,
) {
  const chat = useChatInstance(initial, announce, provider);
  useSyncExternalStore(chat.subscribe, chat.getSnapshot, chat.getServerSnapshot);
  return chat;
}
