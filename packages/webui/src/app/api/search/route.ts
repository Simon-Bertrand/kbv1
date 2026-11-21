import { searchHandlers } from '../../../backend/search';
export const runtime = 'nodejs';
export async function POST(request: Request) { return searchHandlers().search(request); }
