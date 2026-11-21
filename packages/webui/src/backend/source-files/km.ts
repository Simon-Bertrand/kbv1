import { getClientIp } from '../logger';

/** Proxy an already-authorized KM document URL, without forwarding browser credentials.
 * Automatically recovers if upstream reports an expired token and a retry callback is provided.
 * Enforces anti-usurpation by setting verified X-Request-Id, X-Real-IP and X-Authenticated-User.
 */
export async function proxyKmPdf(
  request: Request,
  url: string,
  fetchFile: typeof fetch = fetch,
  options?: {
    onExpiredRetry?: () => Promise<string | null>;
    user?: { id?: string; name?: string | null; email?: string | null } | string | null;
  },
) {
  const range = request.headers.get('range');
  if (range && !/^bytes=(?:\d+-\d*|-\d+)$/.test(range)) {
    return new Response(null, {
      status: 416,
      headers: {
        'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff',
      },
    });
  }

  const requestId = request.headers.get('x-request-id') || crypto.randomUUID();
  const clientIp = getClientIp(request);
  const userIdentifier = typeof options?.user === 'string'
    ? options.user.trim()
    : (options?.user?.email || options?.user?.name || options?.user?.id || 'anonymous');

  const reqHeaders: Record<string, string> = {
    'Accept-Encoding': 'identity',
    'X-Request-Id': requestId,
    'X-Real-IP': clientIp,
    'X-Authenticated-User': userIdentifier,
    ...(range ? { Range: range } : {}),
  };

  const headers = new Headers({
    'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': "sandbox; default-src 'none'",
    'X-Request-Id': requestId,
  });
  const empty = (status: number, extraHeaders?: Record<string, string>) => {
    const resHeaders = new Headers(headers);
    if (extraHeaders) {
      for (const [k, v] of Object.entries(extraHeaders)) resHeaders.set(k, v);
    }
    return new Response(null, { status, headers: resHeaders });
  };

  async function fetchTarget(targetUrl: string): Promise<Response> {
    return fetchFile(targetUrl, {
      method: request.method,
      headers: reqHeaders,
      redirect: 'manual', cache: 'no-store',
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(120000)]),
    });
  }

  let upstream: Response;
  try {
    upstream = await fetchTarget(url);
  } catch {
    request.signal.throwIfAborted();
    return empty(503);
  }

  // Handle expired token from upstream (e.g. fileserver)
  const isExpired = upstream.status === 401 && (
    upstream.headers.get('x-token-status') === 'expired' ||
    (upstream.headers.get('www-authenticate') || '').includes('token_expired')
  );

  if (isExpired) {
    await upstream.body?.cancel();
    if (options?.onExpiredRetry) {
      try {
        const freshUrl = await options.onExpiredRetry();
        if (freshUrl) {
          upstream = await fetchTarget(freshUrl);
        } else {
          return empty(401, {
            'X-Token-Status': 'expired',
            'WWW-Authenticate': 'Bearer error="token_expired"',
          });
        }
      } catch {
        return empty(401, {
          'X-Token-Status': 'expired',
          'WWW-Authenticate': 'Bearer error="token_expired"',
        });
      }
    } else {
      return empty(401, {
        'X-Token-Status': 'expired',
        'WWW-Authenticate': 'Bearer error="token_expired"',
      });
    }
  }

  if (upstream.status === 416) {
    const value = upstream.headers.get('content-range');
    if (value && /^bytes \*\/\d+$/.test(value)) headers.set('Content-Range', value);
    await upstream.body?.cancel();
    return empty(416);
  }
  if (![200, 206].includes(upstream.status) ||
      upstream.headers.get('content-type')?.split(';')[0]?.trim().toLowerCase() !== 'application/pdf' ||
      ![null, 'identity'].includes(upstream.headers.get('content-encoding'))) {
    await upstream.body?.cancel();
    return empty(upstream.status === 404 ? 404 : upstream.status === 403 ? 403 : 502);
  }
  headers.set('Content-Type', 'application/pdf');
  headers.set('Content-Disposition', 'inline');
  for (const name of ['content-length', 'content-range', 'accept-ranges']) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (request.method === 'HEAD') await upstream.body?.cancel();
  return new Response(request.method === 'HEAD' ? null : upstream.body, { status: upstream.status, headers });
}
