'use client';
import { useEffect, useRef, useState } from 'react';
import { useUiClient, useUiClientSelector } from '../providers/client-provider';
import { Button } from '../ui/button';
import copy from '../../content/fr.json';
export function ClientExample() {
  const client = useUiClient();
  const theme = useUiClientSelector((state) => state.theme);
  const [pending, setPending] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const run = async () => {
    const controller = new AbortController();
    request.current = controller;
    setPending(true);
    try {
      await client.request('/api/ui-demo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ theme: theme === 'light' ? 'dark' : 'light' }),
        signal: controller.signal,
        channel: 'catalog',
      });
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError'))
        client.notifications.show(copy.client.requestFailed, 'error');
    } finally {
      if (!controller.signal.aborted) setPending(false);
    }
  };
  return (
    <section className="card">
      <div className="eyebrow">{copy.client.catalogLabel}</div>
      <h2>{copy.client.catalogTitle}</h2>
      <p className="muted">{copy.client.catalogDescription}</p>
      <Button id="client-example" disabled={pending} onClick={() => void run()}>
        {pending ? copy.client.running : copy.client.runExample}
      </Button>
    </section>
  );
}
