import { createHash } from 'node:crypto';
import { createFileJwt, filePath } from './jwt';
import { readJson } from '../http/json';
import { currentRequestId, upstreamHeaders } from '../logger';
import { fileserverPublicBase } from './public-base';

/** Metadata only. The browser downloads PDF bytes directly from Fileserver. */
export async function issuePdfAccess(options: {
  documentId: string; sourceSha256?: string; principal: string; signal: AbortSignal;
  env: Record<string, string | undefined>; fetch: typeof fetch; now: () => number;
}) {
  const { env, documentId, principal, signal, now } = options;
  const origin = (value: string | undefined) => {
    const url = new URL(value ?? '');
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
        url.pathname !== '/' || url.search || url.hash) throw new Error('Invalid source service origin');
    return url.origin;
  };
  const referenceUrl = new URL(`/documents/${documentId}/source-reference`, origin(env.WEBUI_KM_API_URL));
  if (options.sourceSha256 !== undefined) {
    if (!/^[a-f0-9]{64}$/.test(options.sourceSha256)) throw new Error('Invalid source digest');
    referenceUrl.searchParams.set('source_sha256', options.sourceSha256);
  }
  const response = await options.fetch(referenceUrl.href, {
    headers: upstreamHeaders({ Accept: 'application/json', ...(env.KM_API_TOKEN ? {Authorization:'Bearer '+env.KM_API_TOKEN} : {}) }), redirect: 'manual', cache: 'no-store',
    signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
  });
  if (!response.ok || response.headers.get('content-type')?.split(';')[0] !== 'application/json') {
    await response.body?.cancel();
    throw new Error('Source reference unavailable');
  }
  const reference = await readJson(response.body, 16384, () => new Error('Invalid source reference')) as Record<string, unknown>;
  if (!reference || reference.document_id !== documentId) throw new Error('Invalid document reference');
  const path = filePath(reference.relative_path);
  const parts = path.split('/');
  const version = parts.at(-1)!;
  if (parts[0] !== '_versions' || !/^[a-f0-9]{64}\.pdf$/.test(version) ||
      (parts.length !== 2 && !parts.at(-2)?.toLowerCase().endsWith('.pdf')) ||
      (options.sourceSha256 !== undefined && version !== `${options.sourceSha256}.pdf`)) {
    throw new Error('Immutable PDF version required');
  }
  return signAccess(path);

  function signAccess(path: string) {
    const url = fileserverPublicBase(env.FILESERVER_PUBLIC_URL);
    const seconds = Math.floor(now() / 1000);
    const token = createFileJwt({ path, principal: createHash('sha256').update(principal).digest('hex'), requestId: currentRequestId(), secret: env.FILESERVER_JWT_SECRET ?? '', expiresInSeconds: 300, now: () => seconds });
    url.pathname = url.pathname.replace(/\/$/, '') + '/files/' + path.split('/').map(encodeURIComponent).join('/');
    url.searchParams.set('token', token);
    return Response.json({ url: url.href, expiresAt: (seconds + 300) * 1000 }, {
      headers: { 'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff' },
    });
  }
}
