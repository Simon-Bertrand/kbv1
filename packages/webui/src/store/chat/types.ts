export interface Source {
  id: number;
  title: string;
  category: string;
  typeDocumentId?: number;
  typeDocumentLabel?: string;
  excerpt: string;
  pages: string;
  pdf?: { url: string; page: number; accessUrl?: string };
}
export type ActivityPhase = 'analysis' | 'search' | 'reading' | 'synthesis' | 'verification';

export interface ActivityStep {
  id: string;
  type: 'phase' | 'tool';
  phase: ActivityPhase;
  label: string;
  status: 'running' | 'complete' | 'error';
  detail?: string;
  count?: number;
  durationMs?: number;
  timestamp: number;
}

export interface ChatProgress {
  status: 'connecting' | 'running' | 'reconnecting' | 'complete' | 'failed' | 'interrupted';
  summary: string;
  steps: readonly ActivityStep[];
  elapsedSeconds?: number;
}

export interface ResponseComparison {
  id: string;
  responseA: string;
  sourcesA: Source[];
  responseB: string;
  sourcesB: Source[];
  state: 'generating' | 'ready' | 'error';
  choice?: 'a' | 'b';
  activity?: ChatProgress;
}
export interface ChatTurn {
  role: 'user' | 'assistant';
  content: string;
}
export interface Message extends ChatTurn {
  id: string;
  sources?: Source[];
  interrupted?: boolean;
  error?: boolean;
  errorMessage?: string;
  generating?: boolean;
  comparison?: ResponseComparison;
  context?: ChatTurn[];
  activity?: ChatProgress;
  mode?: 'fast' | 'deep';
  fastId?: string;
  fastAnswer?: string;
  fastSources?: Source[];
  escalating?: boolean;
}
export interface Conversation {
  remoteStub?: boolean;
  updatedAt?:string;
  revision?: number;
  deletedAt?: string;
  id: string;
  title: string;
  messages: Message[];
  draft?: string;
  scrollTop?: number;
}
export interface ChatRequest {
  messages: readonly ChatTurn[];
  attachments: readonly File[];
  signal: AbortSignal;
  variant?: 'alternative';
  mode?: 'fast' | 'deep';
  fastId?: string;
  fastAnswer?: string;
  reason?: string;
  onProgress?: (progress: ChatProgress) => void;
}
/** A production adapter must call your own authenticated backend, never a provider secret. */
export interface ConversationPersistence {
  load(signal: AbortSignal): Promise<Conversation[]>;
  save(conversations: readonly Conversation[], onSaved?: (ack: {id:string;revision:number}) => void): Promise<readonly {id:string;revision:number}[] | void>;
  readonly deletedIds?: readonly string[];
  get?(id:string,signal:AbortSignal):Promise<Conversation>;
  deleteEmpty?(id: string): Promise<void>;
}
export interface ChatProvider {
  readonly persistence?: ConversationPersistence;
  readonly origin?: 'demo' | 'generated';
  readonly storageScope?: string;
  stream(request: ChatRequest): AsyncIterable<string>;
  sources: readonly Source[];
}
