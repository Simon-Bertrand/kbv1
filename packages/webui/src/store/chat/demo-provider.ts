import localeCopy from '../../content/fr.json';
const {demoResponses:demoResponsesCopy}=localeCopy;
import { formatText } from '../../content/text';
import type { ChatProvider, ChatRequest } from './types';
import { sources } from './demo-data';
export class DemoProvider implements ChatProvider {
  readonly sources = sources;
  async *stream({ messages, signal, attachments, variant }: ChatRequest): AsyncIterable<string> {
    const prompt =
      messages
        .filter((m) => m.role === 'user')
        .at(-1)
        ?.content.toLowerCase() ?? '';
    const answer =
      variant === 'alternative'
        ? demoResponsesCopy.voiciUneAutreVersionPlusDirectePour
        : prompt.includes('code') || prompt.includes('token')
          ? demoResponsesCopy.lesCouleursSontCentraliseesDansThemeCss
          : formatText(demoResponsesCopy.summary, {
              attachments: attachments.length
                ? formatText(demoResponsesCopy.attachmentsSummary, { count: attachments.length })
                : '',
            });
    for (const token of answer.match(/\S+\s*/g) ?? []) {
      signal.throwIfAborted();
      await new Promise<void>((resolve, reject) => {
        const done = () => {
          signal.removeEventListener('abort', abort);
          resolve();
        };
        const timer = setTimeout(done, 24);
        const abort = () => {
          clearTimeout(timer);
          reject(new DOMException('Aborted', 'AbortError'));
        };
        signal.addEventListener('abort', abort, { once: true });
      });
      yield token;
    }
  }
}
