import type { Source } from '../chat/types';
export interface EvaluationCandidate { id: string; text: string; origin: 'generated' | 'human' | 'demo'; }
export type Evaluation = { mode: 'ppo'; score: number } | { mode: 'dpo'; comparisonId?: string; preference: 'a' | 'b' | 'tie' | 'neither'; alternative: EvaluationCandidate };

export interface ResponseSnapshot {
  conversationId: string;
  messageId: string;
  prompt: string;
  context: { role: 'user' | 'assistant'; content: string }[];
  response: string;
  sources: Source[];
  origin: 'demo' | 'generated';
}
export interface FeedbackValues {
  sentiment: 'positive' | 'negative';
  satisfaction: number | null;
  relevance: number | null;
  accuracy: number | null;
  grounding: number | null;
  issues: string[];
  sourceId: number | null;
  passage: string;
  correction: string;
}
interface AnnotationBase {
  schemaVersion: 1;
  id: string;
  createdAt: string;
  updatedAt: string;
  snapshot: ResponseSnapshot;
  comment: string;
}
export type Annotation = AnnotationBase & (
  | { kind: 'feedback'; values: FeedbackValues }
  | { kind: 'evaluation'; values: Evaluation }
);
