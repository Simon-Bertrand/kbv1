import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentProxy,
} from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { FileText, LoaderCircle } from "lucide-react";
import type { Geometry } from "./client/types.gen";

GlobalWorkerOptions.workerSrc = workerUrl;
export type Highlight = {
  id: string;
  label?: string;
  color: string;
  locations: Geometry[];
  selected?: boolean;
};

export function PdfViewer({
  url,
  onPageCount,
  onPageChange,
  page,
  zoom,
  highlights,
  onHighlight,
}: {
  url: string | null;
  onPageCount: (count: number) => void;
  page: number;
  zoom: number;
  onPageChange: (page: number) => void;
  highlights: Highlight[];
  onHighlight: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [availableWidth, setAvailableWidth] = useState(600);
  const [error, setError] = useState("");
  const [pageSize, setPageSize] = useState({width: 600, height: 800});
  const slots = useRef<(HTMLDivElement | null)[]>([]);
  const reportedPage = useRef(1);
  const frame = useRef(0);
  const [passwordRequired, setPasswordRequired] = useState(false);
  const [password, setPassword] = useState('');
  const submitPassword = useRef<((password: string) => void) | null>(null);
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(([entry]) =>
      setAvailableWidth(Math.max(200, entry.contentRect.width - 72)),
    );
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    setPdf(null);
    setError("");
    setPasswordRequired(false);
    setPassword('');
    if (!url) return;
    let active = true;
    const task = getDocument({ url, cMapUrl: '/pdfjs/cmaps/', cMapPacked: true,
      standardFontDataUrl: '/pdfjs/standard_fonts/', wasmUrl: '/pdfjs/wasm/' });
    task.onPassword = (updatePassword: (password: string) => void) => {
      if (!active) return;
      submitPassword.current = updatePassword;
      setPasswordRequired(true);
      setPassword('');
    };
    task.promise
      .then(async (doc) => {
        const firstPage = await doc.getPage(1);
        const viewport = firstPage.getViewport({scale: 1});
        if (active) {
          setPageSize({width: viewport.width, height: viewport.height});
          setPdf(doc);
          setPasswordRequired(false);
          // Navigation does not require a second PDF parser or fetching every page.
          onPageCount(doc.numPages);
        }
      })
      .catch((err) => {
        if (active) setError(String(err));
      });
    return () => {
      active = false;
      submitPassword.current = null;
      void task.destroy();
    };
  }, [url, onPageCount]);
  useEffect(() => {
    if (!pdf || reportedPage.current === page) return;
    reportedPage.current = page;
    const slot = slots.current[page - 1];
    if (slot && container.current) container.current.scrollTo({top: slot.offsetTop - 24});
  }, [page, pdf]);
  useLayoutEffect(() => {
    const slot = slots.current[reportedPage.current - 1];
    if (pdf && slot && container.current) container.current.scrollTo({top: slot.offsetTop - 24});
  }, [zoom, availableWidth, pdf]);
  useEffect(() => () => cancelAnimationFrame(frame.current), []);
  function trackScroll() {
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      const root = container.current;
      if (!root || !pdf) return;
      const position = root.scrollTop + root.clientHeight * 0.3;
      let low = 0, high = pdf.numPages - 1;
      while (low < high) {
        const mid = Math.ceil((low + high) / 2);
        if ((slots.current[mid]?.offsetTop ?? Infinity) <= position) low = mid;
        else high = mid - 1;
      }
      const current = low + 1;
      if (reportedPage.current !== current) {
        reportedPage.current = current;
        onPageChange(current);
      }
    });
  }
  return (
    <div className="pdf-scroll" ref={container} onScroll={trackScroll} aria-label="Visionneuse PDF">
      {!url ? (
        <div className="viewer-empty">
          <div className="empty-icon">
            <FileText size={35} />
          </div>
          <h2>Votre document, en contexte.</h2>
          <p>
            Choisissez un dossier puis un PDF pour commencer
            <br />à explorer son contenu et ses sources.
          </p>
        </div>
      ) : (
        <>
          {error && (
            <div role="alert" className="error-banner">
              {error}
              <a href={url} target="_blank" rel="noreferrer">Ouvrir le PDF original</a>
            </div>
          )}
          {passwordRequired && (
            <form className="pdf-password" onSubmit={event => {
              event.preventDefault();
              setPasswordRequired(false);
              submitPassword.current?.(password);
              setPassword('');
            }}>
              <label>Mot de passe du PDF<input type="password" autoComplete="off"
                value={password} onChange={event => setPassword(event.target.value)} autoFocus /></label>
              <button type="submit">Ouvrir le PDF</button>
            </form>
          )}
          {!error && !passwordRequired && !pdf && (
            <div className="viewer-loading">
              <LoaderCircle className="spin" size={16} /> Affichage du PDF…
            </div>
          )}
          {pdf && Array.from({length: pdf.numPages}, (_, index) => (
            <div key={index} ref={element => {slots.current[index] = element;}}
              className="pdf-page" data-pdf-page={index + 1} data-current-page={page === index + 1}
              aria-label={`Page ${index + 1}`}>
              <PdfSheet pdf={pdf} page={index + 1} zoom={zoom} availableWidth={availableWidth}
                fallbackSize={pageSize} root={container} highlights={highlights} onHighlight={onHighlight} />
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function PdfSheet({pdf, page, zoom, availableWidth, fallbackSize, root, highlights, onHighlight}: {
  pdf: PDFDocumentProxy; page: number; zoom: number; availableWidth: number;
  fallbackSize: {width: number; height: number}; root: React.RefObject<HTMLDivElement | null>;
  highlights: Highlight[]; onHighlight: (id: string) => void;
}) {
  const sheet = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [near, setNear] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [pageSize, setPageSize] = useState(fallbackSize);
  const dimensions = pageSize;
  const scale = Math.min(availableWidth / pageSize.width, 1.6) * zoom;
  const size = {width: pageSize.width * scale, height: pageSize.height * scale};
  useEffect(() => {
    if (!sheet.current) return;
    const observer = new IntersectionObserver(([entry]) => setNear(entry.isIntersecting),
      {root: root.current, rootMargin: '600px 0px'});
    observer.observe(sheet.current);
    return () => observer.disconnect();
  }, [root]);
  useEffect(() => {
    if (!near || !canvas.current) return;
    let active = true;
    let render:
      | ReturnType<Awaited<ReturnType<PDFDocumentProxy["getPage"]>>["render"]>
      | undefined;
    setBusy(true);
    canvas.current.removeAttribute('data-page');
    setError("");
    void pdf
      .getPage(page)
      .then((pdfPage) => {
        if (!active || !canvas.current) return;
        const initial = pdfPage.getViewport({ scale: 1 });
        const scale = Math.min(availableWidth / initial.width, 1.6) * zoom;
        const viewport = pdfPage.getViewport({ scale });
        // Bound canvas memory for large-format drawings and high zoom levels.
        const ratio = Math.min(window.devicePixelRatio || 1, 2,
          16384 / viewport.width, 16384 / viewport.height,
          Math.sqrt(16_000_000 / (viewport.width * viewport.height)));
        // Render offscreen: cancelled page/zoom jobs never overwrite the current page.
        const target = document.createElement('canvas');
        target.width = Math.floor(viewport.width * ratio);
        target.height = Math.floor(viewport.height * ratio);
        render = pdfPage.render({
          canvas: target,
          viewport,
          transform: [ratio, 0, 0, ratio, 0, 0],
        });
        return render.promise.then(() => {
          if (!active || !canvas.current) return;
          canvas.current.width = target.width;
          canvas.current.height = target.height;
          canvas.current.getContext('2d')!.drawImage(target, 0, 0);
          setPageSize({width: initial.width, height: initial.height});
        });
      })
      .then(() => {
        if (active) {
          canvas.current?.setAttribute("data-page", String(page));
          setBusy(false);
        }
      })
      .catch((err) => {
        if (active && err?.name !== "RenderingCancelledException") {
          setError(String(err));
          setBusy(false);
        }
      });
    return () => {
      active = false;
      render?.cancel();
    };
  }, [pdf, page, availableWidth, zoom, near]);
  // Put smaller, nested regions above their enclosing boxes so they remain clickable.
  const zones = (near ? highlights : [])
    .flatMap((highlight) =>
      highlight.locations
        .filter((location) => location.page_no === page)
        .map((location, index) => ({
          ...highlight,
          location,
          key: `${highlight.id}-${index}`,
        })),
    )
    .sort((a, b) => {
      const area = ({ bbox: [l, t, r, b] }: Geometry) =>
        Math.abs((r - l) * (b - t));
      return area(b.location) - area(a.location);
    });
  return <div ref={sheet}>
          <div
            className="pdf-sheet"
            style={{
              width: size.width || undefined,
              height: size.height || undefined,
            }}
          >
            {near && <canvas ref={canvas} style={{ width: size.width, height: size.height }} />}
            {near && busy && <div className="pdf-page-loading">Affichage de la page {page}…</div>}
            {error && <div role="alert" className="error-banner">{error}</div>}
            {near && dimensions &&
              size.width > 0 &&
              !busy &&
              zones.map((highlight, group) => {
                const loc = highlight.location;
                const [left, top, right, bottom] = loc.bbox;
                const x = Math.min(left, right);
                const y =
                  loc.coord_origin === "BOTTOMLEFT"
                    ? dimensions.height - Math.max(top, bottom)
                    : Math.min(top, bottom);
                return (
                  <button
                    key={highlight.key}
                    className="pdf-highlight"
                    data-testid="pdf-highlight"
                    data-source-ref={highlight.id}
                    title={highlight.label || `Source ${group + 1}`}
                    aria-label={`Sélectionner ${highlight.label || `la zone ${group + 1}`}`}
                    aria-pressed={highlight.selected || false}
                    onClick={() => onHighlight(highlight.id)}
                    style={{
                      left: `${(100 * x) / dimensions.width}%`,
                      top: `${(100 * y) / dimensions.height}%`,
                      width: `${(100 * Math.abs(right - left)) / dimensions.width}%`,
                      height: `${(100 * Math.abs(top - bottom)) / dimensions.height}%`,
                      backgroundColor: `${highlight.color}${highlight.selected ? "40" : "14"}`,
                      borderColor: highlight.color,
                      borderWidth: highlight.selected ? 2.5 : 1,
                    }}
                  />
                );
              })}
          </div>  </div>;
}
