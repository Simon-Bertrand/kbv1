import { chatHandlers } from '../../../../../../backend/chat';

export const runtime = 'nodejs';
export async function GET(request: Request, { params }: { params: Promise<{ token: string }> }) {
  return chatHandlers().events(request, (await params).token);
}
