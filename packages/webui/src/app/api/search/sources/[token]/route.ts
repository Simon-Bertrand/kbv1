import { searchHandlers } from '../../../../../backend/search';
export const runtime = 'nodejs';
async function file(request: Request, { params }: { params: Promise<{ token: string }> }) {
  return searchHandlers().file(request, (await params).token);
}
export { file as GET, file as HEAD };
