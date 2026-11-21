import { chatHandlers } from '../../../../../../../backend/chat';

export const runtime = 'nodejs';
async function file(request: Request, { params }: { params: Promise<{ token: string; sourceId: string }> }) {
  const { token, sourceId } = await params;
  return chatHandlers().file(request, token, sourceId);
}
export { file as GET, file as HEAD };
