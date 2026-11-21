import { chatHandlers } from '../../../../backend/chat';

export const runtime = 'nodejs';
export async function POST(request: Request) {
  return chatHandlers().start(request);
}
