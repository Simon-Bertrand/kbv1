import { authenticateSession } from '../../../../backend/auth';
import { createConversationHandlers } from '../../../../backend/chat/conversation-handlers';

export const runtime = 'nodejs';
const handle = createConversationHandlers(authenticateSession);
export const GET = (request: Request) => handle(request);
export const POST = (request: Request) => handle(request);
