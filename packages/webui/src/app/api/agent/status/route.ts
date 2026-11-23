import { serviceAvailable } from '../../../../backend/readiness';

export const runtime = 'nodejs';

export async function GET() {
  return Response.json({ available: await serviceAvailable('agent') }, {
    headers: { 'Cache-Control': 'no-store' },
  });
}
