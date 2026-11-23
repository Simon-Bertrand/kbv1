'use client';
import { useEffect, useRef, useState } from 'react';
import type { Source } from '../../store/chat/types';
import { sources as demoSources } from '../../store/chat/demo-data';
import copy from '../../content/fr.json';
import { formatText } from '../../content/text';
import { Button } from '../ui/button';
import { Dialog } from '../ui/dialog';
import { Icon } from '../ui/icon';
export function SourceDialog({ source, onClose }: { source: Source | null; onClose: () => void }) {
  const pdf =
    source?.pdf ??
    demoSources.find(
      (candidate) =>
        candidate.id === source?.id &&
        candidate.title === source?.title &&
        candidate.excerpt === source?.excerpt,
    )?.pdf;
  return (
    <Dialog
      id="sources-dialog"
      className={`sources-dialog ${pdf ? 'has-pdf' : ''}`}
      bodyId="sources-content"
      open={!!source}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={<span className="source-title">
        <Icon name="file" />
        <span title={source?.title}>{source?.title ?? copy.navigation.sourceDeLaReponse}</span>
      </span>}
      closeLabel={copy.navigation.fermerLesSources}
      lightDismiss
    >
      {source && <SourceReader key={`${source.id}:${pdf?.url}:${pdf?.page}`} source={source} pdf={pdf} />}
    </Dialog>
  );
}

function SourceReader({ source, pdf }: { source: Source; pdf: Source['pdf'] }) {
  const [excerptOpen, setExcerptOpen] = useState(false);
  const excerptId = `source-${source.id}`;
  const pages = source.pages || (pdf && formatText(copy.sources.opensAtPage, { page: pdf.page }));
  const excerpt = <article className="source-document" id={excerptId} tabIndex={0} aria-labelledby={`${excerptId}-heading`}>
    <header>
      <h3 id={`${excerptId}-heading`}>{copy.sources.excerpt}</h3>
      {pages && <span className="source-pages">{pages}</span>}
    </header>
    <p>{source.excerpt}</p>
  </article>;

  if (!pdf) return excerpt;

  return <div className="source-reader" data-excerpt-open={excerptOpen}>
    <div className="source-mobile-navigation">
      <Button aria-pressed={!excerptOpen} aria-controls="pdf-viewer" onClick={() => setExcerptOpen(false)}>
        {copy.sources.pdf}
      </Button>
      <Button aria-pressed={excerptOpen} aria-controls={excerptId} onClick={() => setExcerptOpen(true)}>
        {copy.sources.excerpt}
      </Button>
    </div>
    {excerpt}
    <PdfDocument reference={pdf} title={source.title} />
  </div>;
}

export function PdfDocument({
  reference,
  title,
  id = 'pdf-viewer',
  highlightCitation = false,
  continuous = false,
  focusPassageId,
  onPassageSelect,
}: {
  reference: NonNullable<Source['pdf']>;
  title: string;
  id?: string;
  highlightCitation?: boolean;
  continuous?: boolean;
  focusPassageId?: string;
  onPassageSelect?: (id: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  useEffect(() => {
    let active = true;
    let viewer: import('./pdf-viewer').PdfViewer | undefined;
    void import('./pdf-viewer')
      .then(({ PdfViewer }) => {
        if (active && host.current) {
          viewer = new PdfViewer(host.current, title);
          setStatus('ready');
          return viewer.open({ ...reference, highlightCitation, continuous, focusPassageId, onPassageSelect });
        }
      })
      .catch(() => {
        if (active) setStatus('error');
      });
    return () => {
      active = false;
      viewer?.dispose();
    };
  }, [reference.url, reference.page, reference.accessUrl, title, highlightCitation, continuous, focusPassageId, onPassageSelect]);
  return (
    <div id={id} className="pdf-document">
      <div className="pdf-viewer-host" ref={host} />
      {status === 'loading' && <p className="pdf-loading muted" role="status">{copy.sources.chargementDuLecteurPdf}</p>}
      {status === 'error' && <p className="pdf-loading error-text" role="alert">{copy.sources.leLecteurPdfNAPasPu}</p>}
    </div>
  );
}
