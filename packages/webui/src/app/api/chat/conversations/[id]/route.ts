import { authenticateSession } from '../../../../../backend/auth';
import { createConversationHandlers } from '../../../../../backend/chat/conversation-handlers';

export const runtime = 'nodejs';
const handle = createConversationHandlers(authenticateSession);
async function action(request: Request, { params }: { params: Promise<{id: string}> }) { return handle(request, (await params).id); }
export { action as GET, action as DELETE };
