import { createHash } from 'node:crypto';
import { redirect } from 'next/navigation';
import type { ReactNode } from 'react';
import { authenticateSession } from '../../backend/auth';
import { createInitialConversation } from '../../backend/initial-conversation';
import { ConnectedChatProvider } from '../../components/chat/connected-provider';
import { AppShell } from '../../components/layout/app-shell';

export default async function WorkspaceLayout({ children }: { children: ReactNode }) {
  const user = await authenticateSession();
  if (!user) redirect('/login');
  const storageScope = createHash('sha256').update(user.id).digest('hex');
  return (
    <ConnectedChatProvider key={storageScope} storageScope={storageScope} initialConversation={createInitialConversation()}>
      <AppShell>{children}</AppShell>
    </ConnectedChatProvider>
  );
}
