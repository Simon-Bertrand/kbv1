import copy from '../../content/fr.json';
import { formatText } from '../../content/text';
import type { ActivityPhase, ActivityStep } from '../../store/chat/types';

const text = copy.agentChat;
const activities = text.activities;

export interface PublicActivityUpdate {
  seq: number;
  summary: string;
  step: ActivityStep;
}


function sanitizeQuery(rawQuery: unknown): string | undefined {
  if (typeof rawQuery !== 'string') return undefined;
  const trimmed = rawQuery.trim();
  if (!trimmed || trimmed.length > 80) return undefined;
  // Forbid URLs
  if (/:\/\/|^(https?|file|ws|ftp):/i.test(trimmed)) return undefined;
  // Forbid IPv4 addresses
  if (/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/.test(trimmed)) return undefined;
  // Forbid host with port / localhost
  if (/localhost(:\d+)?|\b[a-zA-Z0-9.-]+:\d{2,5}\b/i.test(trimmed)) return undefined;
  // Forbid absolute or traversal filesystem paths
  if (/\.\.|\/(home|etc|tmp|usr|var|dev|proc|sys|root|opt)(\/|\b)|\/[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+|[a-zA-Z]:\\/i.test(trimmed)) return undefined;
  // Forbid control characters, HTML/script tags, brackets, braces, backslashes, semicolons, quotes
  if (/[\x00-\x1f\x7f<>{}[\]\\;`"']/.test(trimmed)) return undefined;
  // Strictly allow natural language text: unicode letters, digits, whitespace, and safe punctuation
  if (!/^[\p{L}\p{N}\s_.,?!()'-]+$/u.test(trimmed)) return undefined;
  return trimmed;
}

export function projectAthenaEvent(raw: unknown): PublicActivityUpdate | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const evt = raw as Record<string, unknown>;

  if (typeof evt.seq !== 'number' || !Number.isSafeInteger(evt.seq) || evt.seq < 0 || evt.seq >= Number.MAX_SAFE_INTEGER) {
    return null;
  }
  const seq = evt.seq;

  if (typeof evt.ts !== 'number' || !Number.isFinite(evt.ts) || evt.ts <= 0) {
    return null;
  }
  const timestamp = Math.round(evt.ts * 1000);

  const kind = typeof evt.kind === 'string' ? evt.kind : '';
  const category = typeof evt.category === 'string' ? evt.category : '';
  const name = typeof evt.name === 'string' ? evt.name : '';

  // Category 'llm' (internal llama.cpp inference, prompts, thoughts) is strictly forbidden
  if (category === 'llm') return null;

  // Handle retrieval_result event (e.g. hits returned from RAG)
  if (kind === 'retrieval_result') {
    const returned = typeof evt.returned === 'number' && Number.isSafeInteger(evt.returned) && evt.returned >= 0 && evt.returned <= 10000
      ? evt.returned : 0;
    const spanId = typeof evt.span_id === 'string' && /^[a-zA-Z0-9_-]{1,64}$/.test(evt.span_id)
      ? evt.span_id : `retrieval-${seq}`;
    return {
      seq,
      summary: activities.phaseSearch,
      step: {
        id: spanId,
        type: 'tool',
        phase: 'search',
        label: activities.toolSearchText,
        status: 'complete',
        detail: formatText(activities.passagesFound, { count: returned }),
        count: returned,
        timestamp,
      },
    };
  }

  if (kind !== 'span_start' && kind !== 'span_end') {
    return null;
  }

  const spanId = typeof evt.span_id === 'string' && /^[a-zA-Z0-9_-]{1,64}$/.test(evt.span_id)
    ? evt.span_id : `step-${seq}`;
  const status: 'running' | 'complete' | 'error' = kind === 'span_start'
    ? 'running'
    : evt.status === 'error' ? 'error' : 'complete';

  const durationMs = kind === 'span_end' && typeof evt.duration_ms === 'number' && Number.isFinite(evt.duration_ms) && evt.duration_ms >= 0 && evt.duration_ms <= 3600000
    ? Math.round(evt.duration_ms) : undefined;

  // Nodes (phases)
  if (category === 'node') {
    let phase: ActivityPhase;
    let label: string;

    switch (name) {
      case 'decompose':
      case 'select_subquestion':
        phase = 'analysis';
        label = activities.phaseAnalysis;
        break;
      case 'solve_subquestion':
        phase = 'search';
        label = activities.phaseSearch;
        break;
      case 'synthesize':
        phase = 'synthesis';
        label = activities.phaseSynthesis;
        break;
      case 'verify_final':
      case 'verify':
        phase = 'verification';
        label = activities.phaseVerification;
        break;
      default:
        return null;
    }

    return {
      seq,
      summary: label,
      step: {
        id: spanId,
        type: 'phase',
        phase,
        label,
        status,
        ...(durationMs !== undefined ? { durationMs } : {}),
        timestamp,
      },
    };
  }

  // Tools
  if (category === 'tool') {
    let phase: ActivityPhase;
    let label: string;
    let detail: string | undefined;

    switch (name) {
      case 'Vérifier les services':
        phase = 'analysis';
        label = activities.toolCheckServices;
        break;
      case 'search_text': {
        phase = 'search';
        label = activities.toolSearchText;
        if (evt.inputs && typeof evt.inputs === 'object') {
          const inp = evt.inputs as Record<string, unknown>;
          const rawQuery = inp.question ?? inp.query;
          const query = sanitizeQuery(rawQuery);
          if (query) {
            detail = formatText(activities.queryDetail, { query });
          }
        }
        break;
      }
      case 'search_visual':
        phase = 'search';
        label = activities.toolSearchVisual;
        break;
      case 'list_document_pages':
        phase = 'reading';
        label = activities.toolListPages;
        break;
      case 'read_selected_pages':
      case 'read_page':
        phase = 'reading';
        label = activities.toolReadPages;
        break;
      case 'read_passage':
        phase = 'reading';
        label = activities.toolReadPassage;
        break;
      case 'document_outline':
        phase = 'reading';
        label = activities.toolDocumentOutline;
        break;
      case 'read_document':
        phase = 'reading';
        label = activities.toolReadDocument;
        break;
      case 'read_render':
        phase = 'reading';
        label = activities.toolReadRender;
        break;
      default:
        return null;
    }

    return {
      seq,
      summary: phase === 'analysis' ? activities.phaseAnalysis
        : phase === 'search' ? activities.phaseSearch
        : activities.phaseReading,
      step: {
        id: spanId,
        type: 'tool',
        phase,
        label,
        status,
        ...(detail ? { detail } : {}),
        ...(durationMs !== undefined ? { durationMs } : {}),
        timestamp,
      },
    };
  }

  return null;
}

export function formatSseEvent(id: number | string, event: string, data: unknown): string {
  const serialized = JSON.stringify(data);
  return `id: ${id}\nevent: ${event}\ndata: ${serialized}\n\n`;
}
