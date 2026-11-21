'use client';
import { useEffect, useMemo, useState } from 'react';
import { escapeHtml } from './html';
/** Escape during SSR; load the browser sanitizer after hydration. */
export function Markdown({ text, className = '' }: { text: string; className?: string }) {
  const [render, setRender] = useState<((text: string) => string) | null>(null);
  useEffect(() => {
    let active = true;
    void import('./markdown-renderer')
      .then((module) => {
        if (active) setRender(() => module.renderMarkdown);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  const html = useMemo(() => (render ? render(text) : escapeHtml(text)), [render, text]);
  return <div className={`prose ${className}`} dangerouslySetInnerHTML={{ __html: html }} />;
}
