import localeCopy from '../../content/fr.json';
const {feedbackStorage:feedbackStorageCopy}=localeCopy;
import type { Annotation } from './types';
export const ANNOTATIONS_KEY = 'reno-plus-annotations-v1';
const LIMIT = 500;

/** Local template adapter; replace with authenticated server persistence in production. */
export class AnnotationRepository {
  private records: Annotation[] = [];
  persistent = true;
  constructor(private key = ANNOTATIONS_KEY) {
    try {
      let data: unknown = JSON.parse(localStorage.getItem(this.key) ?? '[]');
      if(data && typeof data==='object' && 'savedAt' in data && 'records' in data) {
        if(typeof data.savedAt!=='number' || Date.now()-data.savedAt>30*86400*1000){localStorage.removeItem?.(this.key);data=[];}
        else data=data.records;
      }
      if (Array.isArray(data)) this.records = data.filter(isAnnotation).slice(-LIMIT);
    } catch { this.persistent = false; }
  }
  all(): readonly Annotation[] { return this.records; }
  feedback(messageId: string, response: string): Extract<Annotation, { kind: 'feedback' }> | undefined {
    return this.records.find((record): record is Extract<Annotation, { kind: 'feedback' }> => record.kind === 'feedback' && record.snapshot.messageId === messageId && record.snapshot.response === response);
  }
  save(record: Annotation): void {
    const index = this.records.findIndex(existing => existing.kind === record.kind && existing.snapshot.messageId === record.snapshot.messageId && existing.snapshot.response === record.snapshot.response && (existing.kind !== 'evaluation' || record.kind !== 'evaluation' || existing.values.mode === record.values.mode && (existing.values.mode !== 'dpo' || record.values.mode !== 'dpo' || existing.values.comparisonId === record.values.comparisonId)));
    const old = this.records[index];
    if (old) record = { ...record, id: old.id, createdAt: old.createdAt };
    const next = [...this.records];
    if (index >= 0) next[index] = record; else next.push(record);
    // Never silently evict previous annotations.
    if (next.length > LIMIT) throw new Error(feedbackStorageCopy.laLimiteLocaleDe500AnnotationsEst);
    try { localStorage.setItem(this.key, JSON.stringify({savedAt:Date.now(),records:next})); this.persistent = true; }
    catch { this.persistent = false; }
    this.records = next;
  }
}
function isAnnotation(value: unknown): value is Annotation {
  if (!value || typeof value !== 'object') return false;
  const record = value as Annotation;
  const snap = record.snapshot;
  if (record.schemaVersion !== 1 || typeof record.id !== 'string' || typeof record.createdAt !== 'string' || typeof record.updatedAt !== 'string' || typeof record.comment !== 'string' || !snap || typeof snap.messageId !== 'string' || typeof snap.conversationId !== 'string' || typeof snap.prompt !== 'string' || typeof snap.response !== 'string' || !Array.isArray(snap.context) || !snap.context.every(message => message && ['user', 'assistant'].includes(message.role) && typeof message.content === 'string') || !Array.isArray(snap.sources) || !['demo', 'generated'].includes(snap.origin)) return false;
  const values = record.values;
  if (!values || typeof values !== 'object') return false;
  if (record.kind === 'feedback') {
    const v = record.values;
    return ['positive', 'negative'].includes(v.sentiment) && (v.satisfaction === null || Number.isInteger(v.satisfaction) && v.satisfaction >= 1 && v.satisfaction <= 5) && [v.relevance, v.accuracy, v.grounding].every(score => score === null || Number.isInteger(score) && score >= 1 && score <= 5) && Array.isArray(v.issues) && v.issues.every(issue => typeof issue === 'string') && (v.sourceId === null || Number.isInteger(v.sourceId)) && typeof v.passage === 'string' && typeof v.correction === 'string';
  }
  if (record.kind !== 'evaluation') return false;
  const v = record.values;
  return v.mode === 'ppo' ? Number.isInteger(v.score) && v.score >= 0 && v.score <= 10 : v.mode === 'dpo' && ['a', 'b', 'tie', 'neither'].includes(v.preference) && !!v.alternative && typeof v.alternative.text === 'string' && typeof v.alternative.id === 'string' && ['generated', 'human', 'demo'].includes(v.alternative.origin);
}

export type ExportFormat = 'annotations' | 'ppo' | 'dpo';
export function exportRows(records: readonly Annotation[], format: ExportFormat): object[] {
  if (format === 'annotations') return [...records];
  return records.flatMap<object>(record => {
    if (record.kind !== 'evaluation') return [];
    const metadata = { annotation_id: record.id, conversation_id: record.snapshot.conversationId, message_id: record.snapshot.messageId, origin: record.snapshot.origin, sources: record.snapshot.sources, comment: record.comment };
    if (format === 'ppo' && record.values.mode === 'ppo') return [{ prompt: record.snapshot.context, response: record.snapshot.response, human_score: record.values.score, score_scale: [0, 10], metadata }];
    if (format === 'dpo' && record.values.mode === 'dpo' && ['a', 'b'].includes(record.values.preference)) {
      const { alternative, preference } = record.values;
      if (!alternative.text.trim() || alternative.text.trim() === record.snapshot.response.trim()) return [];
      return [{ prompt: record.snapshot.context, chosen: [{ role: 'assistant', content: preference === 'a' ? record.snapshot.response : alternative.text }], rejected: [{ role: 'assistant', content: preference === 'a' ? alternative.text : record.snapshot.response }], metadata: { ...metadata, alternative_origin: alternative.origin, comparison_id: record.values.comparisonId } }];
    }
    return [];
  });
}
export function downloadAnnotations(records: readonly Annotation[], format: ExportFormat): boolean {
  const rows = exportRows(records, format);
  if (!rows.length) return false;
  const blob = new Blob([rows.map(row => JSON.stringify(row)).join('\n') + '\n'], { type: 'application/x-ndjson;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = `km-${format}.jsonl`;
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  return true;
}
