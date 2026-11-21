import { marked } from 'marked';
import DOMPurify from 'dompurify';
export function renderMarkdown(text: string): string {
  const html = marked.parse(text, { async: false, breaks: false, gfm: true });
  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['img', 'input', 'style', 'form'],
    FORBID_ATTR: ['style'],
  });
}
