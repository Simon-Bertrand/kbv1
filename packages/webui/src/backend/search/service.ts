import { createHmac, timingSafeEqual } from 'node:crypto';
import copy from '../../content/fr.json';
import { formatText } from '../../content/text';
import { searchDateFields, searchSourcePrefix, searchSourceToken, type DocumentType, type SearchDateFilters, type SearchInput, type SearchResponse } from '../../store/search/types';
import { readJson } from '../http/json';
import { issuePdfAccess } from '../source-files/access';
import { upstreamHeaders } from '../logger';

const text = copy.documentSearch;
const documentId = /^d[a-f0-9]{32}$/;
const passageId = /^c[a-f0-9]{32}$/;
const digestPattern = /^[a-f0-9]{64}$/;

function passageLocations(value: unknown) {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 512).flatMap((location: unknown) => {
    if (!location || typeof location !== 'object') return [];
    const { page_no, bbox, coord_origin } = location as Record<string, unknown>;
    if (typeof page_no !== 'number' || !Number.isSafeInteger(page_no) || page_no < 1 ||
        !Array.isArray(bbox) || bbox.length !== 4 ||
        bbox.some((point) => typeof point !== 'number' || !Number.isFinite(point)) ||
        (coord_origin !== 'TOPLEFT' && coord_origin !== 'BOTTOMLEFT')) return [];
    return [{ page_no, bbox, coord_origin }];
  });
}
export class SearchError extends Error {
  constructor(readonly status: number, message: string) { super(message); }
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new SearchError(502, text.invalidResponse);
  return value as Record<string, unknown>;
}
function bounded(value: unknown, length: number) { return typeof value === 'string' ? value.slice(0, length) : ''; }
function parseDateFilters(value: unknown): SearchDateFilters | undefined {
  if (value === undefined) return;
  const invalid = () => new SearchError(400, text.invalidRequest);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  const input = value as Record<string, unknown>;
  if (Object.keys(input).some((key) => !searchDateFields.some((field) => field === key))) throw invalid();
  const filters: SearchDateFilters = {};
  for (const field of searchDateFields) {
    if (!(field in input)) continue;
    const bounds = input[field];
    if (!bounds || typeof bounds !== 'object' || Array.isArray(bounds)) throw invalid();
    const { from, to } = bounds as Record<string, unknown>;
    const valid = (bound: unknown): bound is string => {
      if (typeof bound !== 'string' || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(bound) || bound.startsWith('0000')) return false;
      const date = new Date(bound);
      return Number.isFinite(date.valueOf()) && date.toISOString().slice(0, 10) === bound;
    };
    if (Object.keys(bounds).some((key) => key !== 'from' && key !== 'to') ||
        (from === undefined && to === undefined) ||
        (from !== undefined && !valid(from)) || (to !== undefined && !valid(to))) throw invalid();
    if (typeof from === 'string' && typeof to === 'string' && from > to) throw invalid();
    filters[field] = { ...(typeof from === 'string' ? { from } : {}), ...(typeof to === 'string' ? { to } : {}) };
  }
  return Object.keys(filters).length ? filters : undefined;
}

export function parseSearchInput(value: unknown): SearchInput {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new SearchError(400, text.invalidRequest);
  const input = value as Record<string, unknown>;
  const limit = input.limit ?? 10;
  const mode = input.mode ?? 'hybrid';
  if (Object.keys(input).some((key) => !['question', 'limit', 'mode', 'type_document_ids', 'perimeter_code', 'date_filters'].includes(key)) ||
      typeof input.question !== 'string' || !input.question.trim() || input.question.length > 4000 ||
      !Number.isInteger(limit) || Number(limit) < 1 || Number(limit) > 100 ||
      typeof mode !== 'string' || !['hybrid', 'semantic', 'lexical'].includes(mode)) throw new SearchError(400, text.invalidRequest);
  const ids = input.type_document_ids;
  const perimeter = input.perimeter_code;
  const dateFilters = parseDateFilters(input.date_filters);
  if ((ids !== undefined && (!Array.isArray(ids) || ids.length > 100 ||
       ids.some((id) => !Number.isSafeInteger(id) || id < 1))) ||
      (perimeter !== undefined && (typeof perimeter !== 'string' || !perimeter.trim() || perimeter.length > 80)))
    throw new SearchError(400, text.invalidRequest);
  return {
    question: input.question.trim(), limit: Number(limit), mode: mode as SearchInput['mode'],
    ...(Array.isArray(ids) && ids.length ? { type_document_ids: [...new Set<number>(ids)] } : {}),
    ...(dateFilters ? { date_filters: dateFilters } : {}),
    ...(typeof perimeter === 'string' ? { perimeter_code: perimeter.trim() } : {}),
  };
}
export function createSearchService(options: {
  env: Record<string, string | undefined>; fetch?: typeof fetch; now?: () => number;
}) {
  const env = options.env;
  const fetchUpstream = options.fetch ?? globalThis.fetch;
  const now = options.now ?? Date.now;
  function origin() {
    try {
      const url = new URL(env.WEBUI_KM_API_URL ?? '');
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
          url.pathname !== '/' || url.search || url.hash) throw new Error();
      return url.origin;
    } catch { throw new SearchError(503, text.unavailable); }
  }
  function sign(payload: string, principal: string) {
    if (!env.WEBUI_AUTH_SECRET || env.WEBUI_AUTH_SECRET.length < 32) throw new SearchError(503, text.unavailable);
    return createHmac('sha256', env.WEBUI_AUTH_SECRET)
      .update(JSON.stringify(['webui-document-search-v1',env.WEBUI_SESSION_EPOCH ?? '0', principal, payload])).digest('hex');
  }
  function issue(id: string, principal: string, passage?: string) {
    const payload = passage ? `${id}.${passage}` : id;
    return `${payload}.${sign(payload, principal)}`;
  }
  return {
    async documentTypes(): Promise<DocumentType[]> {
      const response = await fetchUpstream(`${origin()}/document-types`, {
        headers: upstreamHeaders(env.KM_API_TOKEN ? { Authorization: 'Bearer ' + env.KM_API_TOKEN } : {}),
        redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(3000),
      });
      if (!response.ok) {
        await response.body?.cancel();
        throw new SearchError(503, text.unavailable);
      }
      const { items } = object(await readJson(response.body, 256 * 1024, () => new SearchError(502, text.invalidResponse)));
      if (!Array.isArray(items) || items.length > 1000) throw new SearchError(502, text.invalidResponse);
      return items.map((item) => {
        const { type_document_id, label, perimeter_code, perimeter_label } = object(item);
        if (typeof type_document_id !== 'number' || !Number.isSafeInteger(type_document_id) || type_document_id < 1 ||
            typeof label !== 'string' || !label.trim() || typeof perimeter_code !== 'string' || !perimeter_code.trim() ||
            typeof perimeter_label !== 'string' || !perimeter_label.trim()) throw new SearchError(502, text.invalidResponse);
        return { type_document_id, label, perimeter_code, perimeter_label };
      });
    },
    async search(value: unknown, principal: string, signal: AbortSignal): Promise<SearchResponse> {
      const input = parseSearchInput(value);
      const url = `${origin()}/search`;
      // Fail before a costly embedding request if source capabilities cannot be issued.
      sign('', principal);
      let response: Response;
      try {
        response = await fetchUpstream(url, {
          method: 'POST', headers: upstreamHeaders({ 'Content-Type': 'application/json', Accept: 'application/json', ...(env.KM_API_TOKEN ? {Authorization:'Bearer '+env.KM_API_TOKEN} : {}) }),
          body: JSON.stringify({ ...input, filters: {} }), redirect: 'manual', cache: 'no-store',
          signal: AbortSignal.any([signal, AbortSignal.timeout(60000)]),
        });
      } catch {
        signal.throwIfAborted();
        throw new SearchError(503, text.unavailable);
      }
      if (!response.ok) {
        await response.body?.cancel();
        if (response.status === 422) throw new SearchError(422, text.invalidRequest);
        throw new SearchError(response.status === 429 ? 429 : 503, response.status === 429 ? text.busy : text.unavailable);
      }
      const data = object(await readJson(response.body, 4 * 1024 * 1024, () => new SearchError(502, text.invalidResponse)));
      if ((input.type_document_ids || input.perimeter_code || input.date_filters) && data.scope_applied !== true)
        throw new SearchError(502, text.invalidResponse);
      if (!Array.isArray(data.results) || data.results.length > input.limit) throw new SearchError(502, text.invalidResponse);
      return { results: data.results.map((raw, index) => {
        const hit = object(raw);
        if (typeof hit.document_id !== 'string' || !documentId.test(hit.document_id) ||
            typeof hit.score !== 'number' || !Number.isFinite(hit.score) || typeof hit.text !== 'string')
          throw new SearchError(502, text.invalidResponse);
        const locator = hit.locator ? object(hit.locator) : {};
        const pages = Array.isArray(locator.pages) ? locator.pages : [];
        const page = [locator.page, ...pages].find((page): page is number => Number.isSafeInteger(page) && Number(page) > 0);
        const passage = [hit.id, hit.page_id].find((id): id is string => typeof id === 'string' && passageId.test(id));
        const pdfUrl = searchSourcePrefix + issue(hit.document_id, principal, passage);
        return { documentId: hit.document_id, score: hit.score, source: {
          id: index + 1, title: bounded(hit.original_name, 500) || text.sourceTitle,
          category: text.sourceCategory, excerpt: bounded(hit.text, 20000),
          pages: page ? formatText(text.pageLabel, { page }) : '',
          ...(page ? { pdf: { url: pdfUrl, accessUrl: pdfUrl, page } } : {}),
        } };
      }) };
    },
    async file(request: Request, token: string, principal: string) {
      const match = searchSourceToken.exec(token);
      if (!match) throw new SearchError(404, text.notFound);
      const expected = sign(token.slice(0, token.lastIndexOf('.')), principal);
      if (!timingSafeEqual(Buffer.from(match[3]!, 'hex'), Buffer.from(expected, 'hex')))
        throw new SearchError(404, text.notFound);
      // Each open verifies account and epoch before issuing a short-lived file JWT.
      try {
        const documentId = match[1]!;
        let sourceSha256: string | undefined;
        let locations: ReturnType<typeof passageLocations> = [];
        if (match[2] && passageId.test(match[2])) {
          try {
            const response = await fetchUpstream(`${origin()}/passages/${match[2]}`, {
              headers: upstreamHeaders({ Accept: 'application/json', ...(env.KM_API_TOKEN ? { Authorization: 'Bearer ' + env.KM_API_TOKEN } : {}) }),
              redirect: 'error', cache: 'no-store',
              signal: AbortSignal.any([request.signal, AbortSignal.timeout(2500)]),
            });
            if (response.ok) {
              const passage = object(await readJson(response.body, 1024 * 1024, () => new SearchError(502, text.invalidResponse)));
              if (passage.document_id === documentId && passage.id === match[2]) {
                const record = object(passage.record);
                const digest = passage.source_sha256 ?? record.document_id;
                if (typeof digest === 'string' && digestPattern.test(digest)) {
                  sourceSha256 = digest;
                  locations = passageLocations(record.locations);
                }
              }
            } else {
              await response.body?.cancel();
            }
          } catch {
            request.signal.throwIfAborted();
          }
        }
        const access = await issuePdfAccess({ documentId, sourceSha256, principal, signal: request.signal, env, fetch: fetchUpstream, now });
        return locations.length
          ? Response.json({ ...await access.json(), locations }, { headers: access.headers })
          : access;
      } catch {
        request.signal.throwIfAborted();
        throw new SearchError(503, text.unavailable);
      }
    },
  };
}
