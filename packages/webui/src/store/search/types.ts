import type { Source } from '../chat/types';
export const searchSourcePrefix = '/api/search/sources/';
export const searchSourceToken = /^(d[a-f0-9]{32})(?:\.(c[a-f0-9]{32}|\d{13}))?\.([a-f0-9]{64})$/;
export type SearchMode = 'hybrid' | 'semantic' | 'lexical';
export const searchDateFields = ['edition', 'approval', 'repeal'] as const;
export type SearchDateFilters = Partial<Record<typeof searchDateFields[number], { from?: string; to?: string }>>;
export interface SearchInput {
  question: string;
  limit: number;
  mode: SearchMode;
  type_document_ids?: number[];
  perimeter_code?: string;
  date_filters?: SearchDateFilters;
}
export interface DocumentType {
  type_document_id: number;
  label: string;
  perimeter_code: string;
  perimeter_label: string;
}
export interface SearchHit { documentId: string; score: number; source: Source }
export interface SearchResponse { results: SearchHit[] }
