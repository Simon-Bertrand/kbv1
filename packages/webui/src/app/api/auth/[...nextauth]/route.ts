import { handlers } from '../../../../backend/auth';
import { withRequestContext } from '../../../../backend/logger';

export const runtime = 'nodejs';
export const GET: typeof handlers.GET = (request) =>
  withRequestContext(request, null, () => handlers.GET(request));
export const POST: typeof handlers.POST = (request) =>
  withRequestContext(request, null, () => handlers.POST(request));
