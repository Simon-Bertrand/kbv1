import { createStore } from '../create-store';
import { searchPdfOpenQuery, searchSourcePrefix, searchSourceToken, type SearchHit, type SearchInput, type SearchResponse } from './types';
import copy from '../../content/fr.json';
export interface SearchState {
  status: 'idle' | 'loading' | 'complete' | 'error';
  results: readonly SearchHit[];
  error: string;
}
type Action = { type: 'start' | 'cancel' } | { type: 'complete'; results: SearchHit[] } | { type: 'error'; error: string };
function reducer(state: SearchState, action: Action): SearchState {
  switch (action.type) {
    case 'start': return { status: 'loading', results: [], error: '' };
    case 'cancel': return { ...state, status: 'idle', error: '' };
    case 'complete': return { status: 'complete', results: action.results, error: '' };
    case 'error': return { status: 'error', results: [], error: action.error };
  }
}
/** No singleton, persistence, DOM access or requests at import time. */
export function createSearchClient(fetchSearch: typeof fetch = (...args) => globalThis.fetch(...args)) {
  const store = createStore<SearchState, Action>({ status: 'idle', results: [], error: '' }, reducer);
  let active: AbortController | undefined;
  const cancel = () => { active?.abort(); active = undefined; store.dispatch({ type: 'cancel' }); };
  return {
    ...store, cancel,
    async search(input: SearchInput) {
      active?.abort();
      const request = new AbortController();
      active = request;
      store.dispatch({ type: 'start' });
      try {
        const response = await fetchSearch('/api/search', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(input), signal: request.signal, cache: 'no-store',
        });
        if (!response.ok) throw new Error(response.status === 401 ? copy.documentSearch.unauthorized
          : response.status === 429 ? copy.documentSearch.busy : copy.documentSearch.unavailable);
        const value = await response.json() as SearchResponse;
        if (!value || !Array.isArray(value.results) || value.results.length > input.limit ||
            value.results.some((hit) => !hit || typeof hit.documentId !== 'string' ||
              (hit.passageId !== undefined && (typeof hit.passageId !== 'string' || !/^c[a-f0-9]{32}$/.test(hit.passageId))) ||
              (hit.startOffset !== undefined && (!Number.isSafeInteger(hit.startOffset) || hit.startOffset < 0)) ||
              (hit.pdfOpenUrl !== undefined && (typeof hit.pdfOpenUrl !== 'string' ||
                !hit.pdfOpenUrl.startsWith(searchSourcePrefix) || !hit.pdfOpenUrl.endsWith(searchPdfOpenQuery) ||
                !searchSourceToken.test(hit.pdfOpenUrl.slice(searchSourcePrefix.length, -searchPdfOpenQuery.length)))) ||
              typeof hit.score !== 'number' || !Number.isFinite(hit.score) || !hit.source ||
              !Number.isSafeInteger(hit.source.id) || typeof hit.source.title !== 'string' ||
              typeof hit.source.excerpt !== 'string' || typeof hit.source.pages !== 'string' ||
              typeof hit.source.category !== 'string' || (hit.source.pdf &&
                ((hit.source.pdf.accessUrl !== undefined && hit.source.pdf.accessUrl !== hit.source.pdf.url) || (typeof hit.source.pdf.url !== 'string' || !hit.source.pdf.url.startsWith(searchSourcePrefix) ||
                  !searchSourceToken.test(hit.source.pdf.url.slice(searchSourcePrefix.length).split('?')[0]!)) ||
                  !Number.isSafeInteger(hit.source.pdf.page) || hit.source.pdf.page < 1))))
          throw new Error(copy.documentSearch.invalidResponse);
        if (request.signal.aborted || active !== request) return;
        store.dispatch({ type: 'complete', results: value.results });
      } catch (error) {
        if (request.signal.aborted || active !== request) return;
        store.dispatch({ type: 'error', error: error instanceof Error && Object.values(copy.documentSearch).includes(error.message)
          ? error.message : copy.documentSearch.unavailable });
      } finally {
        if (active === request) active = undefined;
      }
    },
  };
}
