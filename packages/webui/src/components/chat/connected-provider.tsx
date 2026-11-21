'use client';
import { useState, type ReactNode } from 'react';
import { HttpChatProvider } from '../../store/chat/http-provider';
import type { Conversation } from '../../store/chat/types';
import { RenoProvider } from './provider';

export function ConnectedChatProvider({ initialConversation, storageScope, children }: {
  initialConversation: Conversation; storageScope: string; children: ReactNode;
}) {
  const [provider] = useState(() => new HttpChatProvider({ storageScope }));
  return <RenoProvider initialConversation={initialConversation} provider={provider}>{children}</RenoProvider>;
}
