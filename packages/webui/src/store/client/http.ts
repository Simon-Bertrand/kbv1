import { UiClientError } from './protocol';
export interface UiRequestOptions extends RequestInit {
  channel?: string;
}
interface HttpOptions {
  fetch?: typeof fetch;
}
/** One active request per channel; cleanup remains reusable under React Strict Mode. */
export function createUiTransport(receive: (payload: unknown) => void, options: HttpOptions = {}) {
  const pending = new Map<string, AbortController>();
  const cancel = (channel?: string) => {
    if (channel !== undefined) pending.get(channel)?.abort();
    else pending.forEach((controller) => controller.abort());
  };
  async function run<Value>(
    url: string | URL,
    init: UiRequestOptions,
    consume: (response: Response, signal: AbortSignal) => Promise<Value>,
  ): Promise<Value> {
    const { channel = 'ui', signal, ...request } = init;
    const controller = new AbortController();
    const abort = () => controller.abort(signal?.reason);
    if (signal?.aborted) abort();
    controller.signal.throwIfAborted();
    cancel(channel);
    pending.set(channel, controller);
    signal?.addEventListener('abort', abort, { once: true });
    let response: Response | undefined;
    try {
      response = await (options.fetch ?? globalThis.fetch)(url, {
        ...request,
        signal: controller.signal,
      });
      controller.signal.throwIfAborted();
      if (!response.ok) {
        await response.body?.cancel();
        throw new UiClientError(
          'http',
          `UI endpoint returned HTTP ${response.status}.`,
          response.status,
        );
      }
      return await consume(response, controller.signal);
    } finally {
      signal?.removeEventListener('abort', abort);
      if (controller.signal.aborted && response?.body && !response.body.locked)
        await response.body.cancel().catch(() => {});
      if (pending.get(channel) === controller) pending.delete(channel);
    }
  }
  return {
    cancel,
    request: (url: string | URL, init: UiRequestOptions = {}) =>
      run(url, init, async (response, signal) => {
        let payload: unknown;
        try {
          payload = await response.json();
        } catch {
          signal.throwIfAborted();
          throw new UiClientError('protocol', 'Invalid UI endpoint JSON.');
        }
        signal.throwIfAborted();
        receive(payload);
      }),
    stream: (url: string | URL, init: UiRequestOptions = {}) =>
      run(url, init, async (response, signal) => {
        if (!response.body) throw new UiClientError('protocol', 'The UI event stream has no body.');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '',
          batches = 0;
        const line = (value: string) => {
          if (!value.trim()) return;
          if (value.length > 1024 * 1024)
            throw new UiClientError('protocol', 'UI event line is too large.');
          let payload: unknown;
          try {
            payload = JSON.parse(value);
          } catch {
            throw new UiClientError('protocol', 'Invalid UI event JSON.');
          }
          signal.throwIfAborted();
          receive(payload);
          batches++;
        };
        const abort = () => {
          void reader.cancel().catch(() => {});
        };
        signal.addEventListener('abort', abort, { once: true });
        try {
          while (true) {
            signal.throwIfAborted();
            const chunk = await reader.read();
            signal.throwIfAborted();
            buffer += chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });
            let boundary: number;
            while ((boundary = buffer.indexOf('\n')) >= 0) {
              line(buffer.slice(0, boundary));
              buffer = buffer.slice(boundary + 1);
            }
            if (buffer.length > 1024 * 1024)
              throw new UiClientError('protocol', 'UI event line is too large.');
            if (chunk.done) {
              line(buffer);
              break;
            }
          }
          return batches;
        } finally {
          signal.removeEventListener('abort', abort);
          await reader.cancel().catch(() => {});
          reader.releaseLock();
        }
      }),
  };
}
