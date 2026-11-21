import type { ActivityStep, ChatProgress, ChatProvider, ChatRequest, Source } from './types';
import copy from '../../content/fr.json';
import { HttpConversationRepository } from './http-repository';

const text = copy.agentChat;
export class ChatRequestError extends Error {}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ChatRequestError(text.invalidResponse);
  return value as Record<string, unknown>;
}

function sources(value: unknown): Source[] {
  if (!Array.isArray(value) || value.length > 100) throw new ChatRequestError(text.invalidResponse);
  return value.map((input) => {
    const source = object(input);
    if (!Number.isSafeInteger(source.id) || Number(source.id) < 1 ||
        !['title', 'category', 'excerpt', 'pages'].every((key) => typeof source[key] === 'string'))
      throw new ChatRequestError(text.invalidResponse);
    if (source.pdf !== undefined) {
      const pdf = object(source.pdf);
      if (pdf.accessUrl !== undefined && pdf.accessUrl !== pdf.url) throw new Error('Invalid PDF authorization endpoint');
      if (typeof pdf.url !== 'string' || !/^\/api\/chat\/runs\/(?:[a-f0-9]{32}\.\d{13}\.[a-f0-9]{64}|d[a-f0-9]{32}\.[a-f0-9]{64})\/sources\/[1-9]\d{0,2}$/.test(pdf.url) ||
          !Number.isSafeInteger(pdf.page) || Number(pdf.page) < 1) throw new ChatRequestError(text.invalidResponse);
    }
    return structuredClone(source) as unknown as Source;
  });
}

interface SseMessage {
  id?: string;
  event: string;
  data: string;
}

const MAX_LINE_LENGTH = 8 * 1024 * 1024; // 8 MB
const MAX_BUFFER_SIZE = 16 * 1024 * 1024; // 16 MB
const MAX_DATA_SIZE = 8 * 1024 * 1024; // 8 MB

function extractLines(buffer: string, isEof: boolean): { lines: string[]; remaining: string } {
  let processText = buffer;
  let remaining = '';
  // If not EOF and ends with \r, keep \r in remaining because \r\n could span chunk boundary
  if (!isEof && buffer.endsWith('\r')) {
    processText = buffer.slice(0, -1);
    remaining = '\r';
  }
  const lines: string[] = [];
  let start = 0;
  for (let i = 0; i < processText.length; i++) {
    const ch = processText[i];
    if (ch === '\r') {
      if (i + 1 < processText.length && processText[i + 1] === '\n') {
        lines.push(processText.slice(start, i));
        i++; // skip \n
        start = i + 1;
      } else {
        lines.push(processText.slice(start, i));
        start = i + 1;
      }
    } else if (ch === '\n') {
      lines.push(processText.slice(start, i));
      start = i + 1;
    }
  }
  remaining = processText.slice(start) + remaining;
  return { lines, remaining };
}

export async function readSseStream(
  body: ReadableStream<Uint8Array>,
  signal: AbortSignal,
  onMessage: (msg: SseMessage) => boolean | void | Promise<boolean | void>,
): Promise<void> {
  if (signal.aborted) throw signal.reason;
  const reader = body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  let currentId: string | undefined;
  let currentEvent = 'message';
  let currentData = '';
  let shouldStop = false;

  let cleanupAbort: (() => void) | undefined;
  const abortPromise = new Promise<never>((_, reject) => {
    const onAbort = () => {
      reader.cancel(signal.reason).catch(() => {});
      reject(signal.reason);
    };
    signal.addEventListener('abort', onAbort, { once: true });
    cleanupAbort = () => signal.removeEventListener('abort', onAbort);
    if (signal.aborted) onAbort();
  });

  const dispatch = async () => {
    if (currentData) {
      const res = onMessage({ id: currentId, event: currentEvent, data: currentData });
      const result = res instanceof Promise ? await res : res;
      if (result === false || currentEvent === 'complete') {
        shouldStop = true;
      }
    }
    currentId = undefined;
    currentEvent = 'message';
    currentData = '';
  };

  const processLine = async (line: string) => {
    if (line.length > MAX_LINE_LENGTH) throw new ChatRequestError(text.invalidResponse);
    if (!line) {
      await dispatch();
      return;
    }
    if (line.startsWith(':')) return;
    const colonIndex = line.indexOf(':');
    if (colonIndex === -1) return;
    const field = line.slice(0, colonIndex);
    const rawValue = line.slice(colonIndex + 1);
    const val = rawValue.startsWith(' ') ? rawValue.slice(1) : rawValue;

    if (field === 'id') currentId = val;
    else if (field === 'event') currentEvent = val;
    else if (field === 'data') {
      currentData = currentData ? `${currentData}\n${val}` : val;
      if (currentData.length > MAX_DATA_SIZE) throw new ChatRequestError(text.invalidResponse);
    }
  };

  try {
    while (!signal.aborted && !shouldStop) {
      const { value, done } = await Promise.race([
        reader.read(),
        abortPromise,
      ]);
      if (signal.aborted) throw signal.reason;

      if (done) {
        buffer += decoder.decode();
        const { lines } = extractLines(buffer, true);
        for (const line of lines) {
          await processLine(line);
          if (shouldStop) break;
        }
        // Discard any trailing unclosed frame at EOF (incomplete frame without blank line)
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      if (buffer.length > MAX_BUFFER_SIZE) throw new ChatRequestError(text.invalidResponse);

      const { lines, remaining } = extractLines(buffer, false);
      buffer = remaining;

      for (const line of lines) {
        await processLine(line);
        if (shouldStop) break;
      }
    }
  } finally {
    cleanupAbort?.();
    await reader.cancel(signal.reason).catch(() => {});
    reader.releaseLock();
  }
}


export class HttpChatProvider implements ChatProvider {
  readonly origin = 'generated' as const;
  sources: readonly Source[] = [];
  lastFastId?: string;
  lastMode?: 'fast' | 'deep';
  constructor(private options: { fetch?: typeof globalThis.fetch; pollMs?: number; storageScope?: string; sse?: boolean } = {}) {}
  private remote?: HttpConversationRepository;
  get persistence() { return this.remote ??= new HttpConversationRepository(this.options.fetch); }
  get storageScope() { return this.options.storageScope; }

  async *stream({ messages, attachments, signal, onProgress, mode, fastId, fastAnswer, reason }: ChatRequest): AsyncIterable<string> {
    this.sources = [];
    if (attachments.length) throw new ChatRequestError(text.attachmentsUnsupported);
    let questionIndex = messages.length - 1;
    while (questionIndex >= 0 && messages[questionIndex]?.role !== 'user') questionIndex--;
    const question = messages[questionIndex]?.content ?? '';
    // Keep recent turns in order, with bounded context even for very long answers.
    const history: { role: 'user' | 'assistant'; content: string }[] = [];
    let budget = 12000;
    for (const turn of messages.slice(0, questionIndex).slice(-12).reverse()) {
      const content = turn.content.slice(0, Math.min(4000, budget));
      if (content.trim()) history.unshift({ role: turn.role, content });
      budget -= content.length;
      if (!budget) break;
    }
    if (!question.trim() || question.length > 4000) throw new ChatRequestError(text.invalidRequest);
    const fetchResponse = this.options.fetch ?? globalThis.fetch;

    if (mode === 'fast') {
      const fastSignal = AbortSignal.any([signal, AbortSignal.timeout(30000)]);
      let fastResponse: Response;
      try {
        fastResponse = await fetchResponse('/api/chat/fast', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
          body: JSON.stringify({ question, mode: 'fast', ...(history.length ? { history } : {}) }),
          signal: fastSignal,
          credentials: 'same-origin',
          cache: 'no-store',
          redirect: 'error',
        });
      } catch {
        fastSignal.throwIfAborted();
        throw new ChatRequestError(text.unavailable);
      }

      if (!fastResponse.ok) {
        await fastResponse.body?.cancel();
        throw new ChatRequestError(
          fastResponse.status === 401 ? text.unauthorized :
          fastResponse.status === 429 ? text.busy :
          fastResponse.status === 404 ? text.notFound :
          fastResponse.status === 400 ? text.invalidRequest : text.unavailable,
        );
      }

      if (!fastResponse.body) throw new ChatRequestError(text.invalidResponse);

      this.lastMode = 'fast';
      let fastResult: { answer: string; sources: Source[] } | undefined;
      const queue: string[] = [];
      let notify: (() => void) | null = null;
      let streamDone = false;
      let streamError: Error | null = null;

      const readerPromise = readSseStream(fastResponse.body, fastSignal, (msg) => {
        if (msg.event === 'init') {
          try {
            const data = object(JSON.parse(msg.data));
            if (typeof data.id === 'string') this.lastFastId = data.id;
            if (data.sources) this.sources = sources(data.sources);
          } catch {}
        } else if (msg.event === 'delta') {
          try {
            const data = object(JSON.parse(msg.data));
            const delta = typeof data.delta === 'string' ? data.delta : typeof data.content === 'string' ? data.content : '';
            if (delta) {
              queue.push(delta);
              notify?.();
            }
          } catch {}
        } else if (msg.event === 'complete') {
          try {
            const data = object(JSON.parse(msg.data));
            if (typeof data.id === 'string') this.lastFastId = data.id;
            if (typeof data.answer === 'string') {
              fastResult = { answer: data.answer, sources: sources(data.sources) };
              this.sources = fastResult.sources;
            }
          } catch {}
        } else if (msg.event === 'error') {
          try {
            const data = object(JSON.parse(msg.data));
            throw new ChatRequestError(typeof data.error === 'string' ? data.error : text.failed);
          } catch (e) {
            if (e instanceof ChatRequestError) throw e;
            throw new ChatRequestError(text.failed);
          }
        }
      }).then(() => {
        streamDone = true;
        notify?.();
      }, (err) => {
        streamError = err;
        streamDone = true;
        notify?.();
      });

      let yieldedCount = 0;
      while (!streamDone || queue.length > 0) {
        if (queue.length > 0) {
          const chunk = queue.shift()!;
          yieldedCount++;
          yield chunk;
        } else if (!streamDone) {
          await new Promise<void>((resolve) => {
            notify = resolve;
          });
          notify = null;
        }
      }
      await readerPromise;
      if (streamError) throw streamError;
      if (yieldedCount === 0 && fastResult) {
        yield fastResult.answer;
      }
      return;
    }

    this.lastMode = 'deep';
    const requestJson = async (url: string, sig: AbortSignal, init: RequestInit = {}) => {
      const response = await fetchResponse(url, { ...init, signal: sig, credentials: 'same-origin', cache: 'no-store', redirect: 'error' });
      sig.throwIfAborted();
      if (!response.ok) {
        await response.body?.cancel();
        throw new ChatRequestError(response.status === 401 ? text.unauthorized : response.status === 429 ? text.busy :
          response.status === 404 ? text.notFound : response.status === 400 ? text.invalidRequest : text.unavailable);
      }
      return object(await response.json());
    };

    const startPayload: Record<string, unknown> = {
      question,
      mode: 'deep',
      ...(history.length ? { history } : {}),
      ...(fastId ? { fast_id: fastId } : {}),
      ...(fastAnswer ? { fast_answer: fastAnswer } : {}),
      ...(reason ? { reason } : {}),
    };
    const start = await requestJson('/api/chat/runs', AbortSignal.any([signal, AbortSignal.timeout(20000)]), {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(startPayload),
    });

    if (typeof start.id !== 'string' || !/^[a-f0-9]{32}\.\d{13}\.[a-f0-9]{64}$/.test(start.id) ||
        typeof start.timeoutMs !== 'number' || !Number.isFinite(start.timeoutMs) || start.timeoutMs < 1000 || start.timeoutMs > 930000)
      throw new ChatRequestError(text.invalidResponse);

    const timeout = AbortSignal.timeout(start.timeoutMs);
    const streamSignal = AbortSignal.any([signal, timeout]);
    const startTime = Date.now();

    const currentSteps: ActivityStep[] = [];
    let currentSummary = text.activities.initialSummary;

    const seenSeqs = new Set<number>();
    const notifyProgress = (status: ChatProgress['status']) => {
      if (signal.aborted || streamSignal.aborted) return;
      onProgress?.({
        status,
        summary: status === 'reconnecting' ? text.activities.reconnecting : currentSummary,
        steps: [...currentSteps],
        elapsedSeconds: Math.max(0, Math.round((Date.now() - startTime) / 1000)),
      });
    };

    notifyProgress('running');

    const useSse = this.options.sse ?? Boolean(onProgress);
    let completedResult: { answer: string; sources: Source[] } | undefined;
    let sseFailed = !useSse;
    let lastCursor: number | undefined;
    let reconnectAttempts = 0;

    const delay = (ms: number, sig: AbortSignal) => new Promise<void>((resolve, reject) => {
      const onAbort = () => { clearTimeout(timer); sig.removeEventListener('abort', onAbort); reject(sig.reason); };
      const timer = setTimeout(() => { sig.removeEventListener('abort', onAbort); resolve(); }, ms);
      sig.addEventListener('abort', onAbort, { once: true });
      if (sig.aborted) onAbort();
    });

    try {
      while (!completedResult && reconnectAttempts < 3 && !sseFailed) {
        streamSignal.throwIfAborted();
        const sseUrl = `/api/chat/runs/${start.id}/events${lastCursor !== undefined ? `?after=${lastCursor}` : ''}`;
        const headers: Record<string, string> = { 'Accept': 'text/event-stream' };
        if (lastCursor !== undefined) headers['Last-Event-ID'] = String(lastCursor);

        let sseResponse: Response;
        try {
          sseResponse = await fetchResponse(sseUrl, {
            headers,
            signal: streamSignal,
            credentials: 'same-origin',
            cache: 'no-store',
            redirect: 'error',
          });
        } catch {
          streamSignal.throwIfAborted();
          reconnectAttempts++;
          if (reconnectAttempts < 3 && !streamSignal.aborted) {
            notifyProgress('reconnecting');
            await delay(Math.min(2000, 300 * Math.pow(2, reconnectAttempts - 1)), streamSignal);
            continue;
          }
          sseFailed = true;
          break;
        }

        if (!sseResponse.ok) {
          await sseResponse.body?.cancel().catch(() => {});
          if (sseResponse.status === 401 || sseResponse.status === 403) {
            throw new ChatRequestError(text.unauthorized);
          }
          if (sseResponse.status === 429) {
            const retryHeader = sseResponse.headers.get('Retry-After');
            const retrySec = retryHeader ? Number(retryHeader) : 1;
            const waitMs = Math.min(5000, Math.max(1000, (Number.isFinite(retrySec) ? retrySec : 1) * 1000));
            reconnectAttempts++;
            if (reconnectAttempts < 3 && !streamSignal.aborted) {
              notifyProgress('reconnecting');
              await delay(waitMs, streamSignal);
              continue;
            }
            throw new ChatRequestError(text.busy);
          }
          if (sseResponse.status === 404) {
            // Verify if run read is authorized before falling back to polling
            try {
              const probe = await fetchResponse(`/api/chat/runs/${start.id}`, {
                signal: streamSignal, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
              });
              if (!probe.ok) {
                await probe.body?.cancel().catch(() => {});
                throw new ChatRequestError(probe.status === 401 ? text.unauthorized : text.notFound);
              }
              await probe.body?.cancel().catch(() => {});
            } catch (err) {
              if (err instanceof ChatRequestError) throw err;
            }
            sseFailed = true;
            break;
          }
          if (sseResponse.status >= 500) {
            reconnectAttempts++;
            if (reconnectAttempts < 3 && !streamSignal.aborted) {
              notifyProgress('reconnecting');
              await delay(Math.min(2000, 300 * Math.pow(2, reconnectAttempts - 1)), streamSignal);
              continue;
            }
          }
          sseFailed = true;
          break;
        }


        const contentType = sseResponse.headers.get('content-type') || '';
        if (!contentType.includes('text/event-stream')) {
          await sseResponse.body?.cancel().catch(() => {});
          sseFailed = true;
          break;
        }

        if (!sseResponse.body) {
          sseFailed = true;
          break;
        }

        try {
          await readSseStream(sseResponse.body, streamSignal, (msg) => {
            if (msg.event === 'checkpoint') {
              try {
                const data = object(JSON.parse(msg.data));
                if (typeof data.cursor === 'number' && Number.isSafeInteger(data.cursor) && data.cursor >= 0) {
                  lastCursor = Math.max(lastCursor ?? 0, data.cursor);
                }
              } catch {}
              return;
            }
            if (msg.event === 'activity') {
              try {
                const data = object(JSON.parse(msg.data));
                if (typeof data.seq !== 'number' || !Number.isSafeInteger(data.seq) || data.seq < 0 ||
                    data.seq >= Number.MAX_SAFE_INTEGER || (lastCursor !== undefined && data.seq < lastCursor)) {
                  return;
                }
                if (seenSeqs.has(data.seq)) {
                  return;
                }
                seenSeqs.add(data.seq);
                if (seenSeqs.size > 1000) {
                  const first = seenSeqs.values().next().value;
                  if (first !== undefined) seenSeqs.delete(first);
                }
                lastCursor = data.seq + 1;

                if (typeof data.summary === 'string' && data.summary.length <= 255) {
                  currentSummary = data.summary;
                }

                if (data.step && typeof data.step === 'object' && !Array.isArray(data.step)) {
                  const rawStep = data.step as Record<string, unknown>;
                  const stepId = typeof rawStep.id === 'string' && rawStep.id.length <= 128 ? rawStep.id : undefined;
                  const type = rawStep.type === 'phase' || rawStep.type === 'tool' ? rawStep.type
                    : (rawStep.phase === 'analysis' || rawStep.phase === 'synthesis' || rawStep.phase === 'verification' ? 'phase' : 'tool');
                  const phase = ['analysis', 'search', 'reading', 'synthesis', 'verification'].includes(rawStep.phase as string)
                    ? rawStep.phase as ActivityStep['phase'] : undefined;
                  const label = typeof rawStep.label === 'string' && rawStep.label.length <= 255 ? rawStep.label : undefined;
                  const status = rawStep.status === 'running' || rawStep.status === 'complete' || rawStep.status === 'error'
                    ? rawStep.status : undefined;

                  if (stepId && phase && label && status) {
                    const detail = typeof rawStep.detail === 'string' && rawStep.detail.length <= 1000 ? rawStep.detail : undefined;
                    const durationMs = typeof rawStep.durationMs === 'number' && Number.isFinite(rawStep.durationMs) && rawStep.durationMs >= 0 && rawStep.durationMs <= 3600000
                      ? Math.round(rawStep.durationMs) : undefined;
                    const count = typeof rawStep.count === 'number' && Number.isSafeInteger(rawStep.count) && rawStep.count >= 0 && rawStep.count <= 10000
                      ? rawStep.count : undefined;
                    const timestamp = typeof rawStep.timestamp === 'number' && Number.isFinite(rawStep.timestamp) && rawStep.timestamp > 0
                      ? rawStep.timestamp : Date.now();

                    const validatedStep: ActivityStep = {
                      id: stepId,
                      type,
                      phase,
                      label,
                      status,
                      ...(detail !== undefined ? { detail } : {}),
                      ...(durationMs !== undefined ? { durationMs } : {}),
                      ...(count !== undefined ? { count } : {}),
                      timestamp,
                    };

                    const existingIndex = currentSteps.findIndex((s) => s.id === validatedStep.id);
                    if (existingIndex >= 0) {
                      const existing = currentSteps[existingIndex];
                      if (existing) {
                        currentSteps[existingIndex] = {
                          ...existing,
                          ...validatedStep,
                          detail: validatedStep.detail ?? existing.detail,
                          count: validatedStep.count ?? existing.count,
                        };
                      }
                    } else {
                      currentSteps.push(validatedStep);
                      if (currentSteps.length > 40) currentSteps.shift();
                    }
                  }
                }
                notifyProgress('running');
              } catch {
                // Ignore malformed activity frame
              }
            } else if (msg.event === 'complete') {
              const data = object(JSON.parse(msg.data));
              if (typeof data.answer !== 'string' || !data.answer.trim()) throw new ChatRequestError(text.invalidResponse);
              completedResult = { answer: data.answer, sources: sources(data.sources) };
              currentSummary = text.activities.completed;
              notifyProgress('complete');
            } else if (msg.event === 'error') {
              const data = object(JSON.parse(msg.data));
              if (data.interrupted) {
                currentSummary = text.activities.interrupted;
                notifyProgress('interrupted');
                throw new DOMException('Generation interrupted', 'AbortError');
              }
              currentSummary = text.activities.failed;
              notifyProgress('failed');
              throw new ChatRequestError(typeof data.error === 'string' ? data.error : text.failed);
            }
          });
        } catch (error) {
          streamSignal.throwIfAborted();
          if (error instanceof ChatRequestError || (error instanceof DOMException && error.name === 'AbortError')) {
            throw error;
          }
          // Network dropped or parser issue mid-stream: retry reconnect with cursor
        }

        if (completedResult) break;
        reconnectAttempts++;
        if (reconnectAttempts < 3 && !streamSignal.aborted) {
          notifyProgress('reconnecting');
          await delay(Math.min(2000, 300 * Math.pow(2, reconnectAttempts - 1)), streamSignal);
        }
      }

      // If SSE completed, yield and finish
      if (completedResult) {
        this.sources = completedResult.sources;
        streamSignal.throwIfAborted();
        yield completedResult.answer;
        return;
      }

      // Fallback: poll /api/chat/runs/${start.id} if SSE disconnected or unavailable
      currentSummary = text.activities.fallbackSummary || currentSummary;
      notifyProgress('running');
      while (true) {
        streamSignal.throwIfAborted();
        const result = await requestJson(`/api/chat/runs/${start.id}`, streamSignal);
        if (result.status === 'complete') {
          if (typeof result.answer !== 'string' || !result.answer.trim()) throw new ChatRequestError(text.invalidResponse);
          this.sources = sources(result.sources);
          currentSummary = text.activities.completed;
          notifyProgress('complete');
          streamSignal.throwIfAborted();
          yield result.answer;
          return;
        }
        if (result.status === 'failed') {
          currentSummary = text.activities.failed;
          notifyProgress('failed');
          throw new ChatRequestError(text.failed);
        }
        if (result.status !== 'running') throw new ChatRequestError(text.invalidResponse);

        notifyProgress('running');
        await delay(this.options.pollMs ?? 1000, streamSignal);
      }
    } catch (error) {
      signal.throwIfAborted();
      if (timeout.aborted) {
        currentSummary = text.timeout;
        notifyProgress('failed');
        throw new ChatRequestError(text.timeout);
      }
      throw error;
    }
  }
}
