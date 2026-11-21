import localeCopy from '../../content/fr.json';
const {common:commonCopy, pdf:pdfCopy}=localeCopy;
import { escapeHtml } from '../ui/html';
import { formatText } from '../../content/text';
import { getElement } from '../ui/dom';
import { getDocument, GlobalWorkerOptions, TextLayer } from 'pdfjs-dist';
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import { icon } from '../ui/icons/icon';
GlobalWorkerOptions.workerSrc = '/pdfjs/pdf.worker.min.mjs';

export interface PdfDocumentReference {
  url: string;
  page: number;
  accessUrl?: string;
  onRefresh?: () => Promise<string | null>;
  highlightCitation?: boolean;
}

interface PassageLocation {
  page_no: number;
  bbox: [number, number, number, number];
  coord_origin: 'TOPLEFT' | 'BOTTOMLEFT';
}
export function sourcePdfUrl(raw: string): URL | null {
  try {
    const url = new URL(raw, location.href);
    return url.origin === location.origin && ['http:', 'https:'].includes(url.protocol)
      ? url
      : null;
  } catch {
    return null;
  }
}

/** A local PDF.js worker renders only the cited document. No PDF scripts/forms execute. */
export class PdfViewer {
  private accessAbort?: AbortController;
  private renewed = false;
  private loading?: PDFDocumentLoadingTask;
  private document?: PDFDocumentProxy;
  private task?: RenderTask;
  private textLayer?: TextLayer;
  private observer: ResizeObserver;
  private version = 0;
  private currentPage = 1;
  private reference?: PdfDocumentReference;
  private citationLocations: PassageLocation[] = [];
  private revealedPage?: number;
  private url?: URL;
  private zoom = 'fit';
  private disposed = false;
  private resizeTimer?: ReturnType<typeof setTimeout>;
  private hostWidth = 0;
  private hostHeight = 0;
  constructor(
    private host: HTMLElement,
    private title: string,
  ) {
    host.innerHTML = `
      <section class="pdf-viewer" aria-label="${escapeHtml(pdfCopy.lecteurPdf)}">
        <div class="pdf-toolbar">
          <div class="pdf-navigation">
            <button type="button" class="icon-button" data-pdf-prev aria-label="${escapeHtml(pdfCopy.pagePrecedente)}">${icon('chevron')}</button>
            <form class="pdf-page-form">
              <label class="sr-only" for="pdf-page">${escapeHtml(pdfCopy.pageDuPdf)}</label>
              <input class="input" id="pdf-page" name="page" type="number" min="1" step="1" required>
              <span data-pdf-total></span>
              <button class="sr-only" type="submit" tabindex="-1">${escapeHtml(pdfCopy.allerALaPage)}</button>
            </form>
            <button type="button" class="icon-button" data-pdf-next aria-label="${escapeHtml(pdfCopy.pageSuivante)}">${icon('chevron')}</button>
          </div>
          <div class="pdf-tools">
            <label class="sr-only" for="pdf-zoom">${escapeHtml(pdfCopy.zoomDuPdf)}</label>
            <select id="pdf-zoom" class="input">
              <option value="fit">${escapeHtml(pdfCopy.ajusterALaLargeur)}</option>
              <option value="page">${escapeHtml(pdfCopy.ajusterALaPage)}</option>
              <option value="1">${escapeHtml(pdfCopy.label100)}</option>
              <option value="1.25">${escapeHtml(pdfCopy.label125)}</option>
              <option value="1.5">${escapeHtml(pdfCopy.label150)}</option>
              <option value="2">${escapeHtml(pdfCopy.label200)}</option>
            </select>
            <a class="icon-button" data-pdf-open target="_blank" rel="noopener noreferrer" hidden
              aria-label="${escapeHtml(pdfCopy.ouvrirLePdf)}" title="${escapeHtml(pdfCopy.ouvrirLePdf)}">${icon('external')}</a>
          </div>
        </div>
        <p class="pdf-citation-note muted" data-pdf-citation hidden></p>
        <div class="pdf-stage" tabindex="0" aria-label="${escapeHtml(pdfCopy.pageDuDocument)}">
          <div class="pdf-feedback">
            <p class="pdf-status muted" role="status" data-pdf-status>${escapeHtml(pdfCopy.chargementDuPdf)}</p>
            <p class="pdf-error error-text" role="alert" data-pdf-error hidden></p>
            <button class="button" type="button" data-pdf-retry hidden>${escapeHtml(commonCopy.retry)}</button>
          </div>
          <div class="pdf-paper" hidden><canvas aria-hidden="true"></canvas><div class="pdf-citation-layer" aria-hidden="true"></div><div class="pdf-text-layer" role="document"></div></div>
        </div>
      </section>`;
    getElement('[data-pdf-prev]', host).addEventListener('click', () => {
      void this.render(this.currentPage - 1);
    });
    getElement('[data-pdf-next]', host).addEventListener('click', () => {
      void this.render(this.currentPage + 1);
    });
    const pageForm = getElement<HTMLFormElement>('form', host);
    pageForm.addEventListener('submit', (event) => {
      event.preventDefault();
      void this.render(this.pageInput.valueAsNumber);
    });
    this.pageInput.addEventListener('change', () => pageForm.requestSubmit());
    getElement<HTMLSelectElement>('#pdf-zoom', host).addEventListener('change', (event) => {
      this.zoom = (event.target as HTMLSelectElement).value;
      void this.render(this.currentPage);
    });
    getElement('[data-pdf-retry]', host).addEventListener('click', () => {
      if (this.reference) void this.open(this.reference);
    });
    getElement('.pdf-stage', host).addEventListener('keydown', (event) => {
      if (event.target !== event.currentTarget || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      void this.render(this.currentPage + (event.key === 'ArrowLeft' ? -1 : 1));
    });
    this.observer = new ResizeObserver((entries) => {
      const width = Math.round(entries[0]?.contentRect.width ?? 0);
      const height = Math.round(entries[0]?.contentRect.height ?? 0);
      if (width === this.hostWidth && height === this.hostHeight) return;
      this.hostWidth = width;
      this.hostHeight = height;
      clearTimeout(this.resizeTimer);
      if (width && height && this.document && (this.zoom === 'fit' || this.zoom === 'page'))
        this.resizeTimer = setTimeout(() => {
          void this.render(this.currentPage);
        }, 120);
    });
    this.observer.observe(host);
    this.controls(true);
  }
  private get pageInput(): HTMLInputElement {
    return getElement<HTMLInputElement>('#pdf-page', this.host);
  }
  private status(text: string): void {
    getElement('[data-pdf-status]', this.host).textContent = text;
  }
  private controls(busy: boolean): void {
    const previous = getElement<HTMLButtonElement>('[data-pdf-prev]', this.host);
    const next = getElement<HTMLButtonElement>('[data-pdf-next]', this.host);
    const stage = getElement('.pdf-stage', this.host);
    const atStart = !this.document || this.currentPage <= 1;
    const atEnd = !this.document || this.currentPage >= this.document.numPages;
    if ((atStart && previous.matches(':focus')) || (atEnd && next.matches(':focus')))
      stage.focus({ preventScroll: true });
    previous.disabled = atStart;
    next.disabled = atEnd;
    this.pageInput.disabled = !this.document;
    getElement<HTMLSelectElement>('#pdf-zoom', this.host).disabled = !this.document;
    stage.setAttribute('aria-busy', String(busy));
  }
  async open(reference: PdfDocumentReference, renewing = false): Promise<void> {
    if (!renewing) this.renewed = false;
    this.accessAbort?.abort();
    // Old saved citations contain the same authorization URL without accessUrl.
    const local = sourcePdfUrl(reference.url);
    if (!reference.accessUrl && local && /^\/api\/(?:chat\/runs\/[^/]+\/sources\/[1-9]\d{0,2}|search\/sources\/[^/]+)$/.test(local.pathname))
      reference = { ...reference, accessUrl: reference.url };
    this.reference = { ...reference };
    this.citationLocations = [];
    this.revealedPage = undefined;
    getElement('[data-pdf-citation]', this.host).hidden = true;
    let url = sourcePdfUrl(reference.accessUrl ?? reference.url);
    if (!url) {
      this.fail(pdfCopy.lePdfDoitEtreServiParCette);
      return;
    }
    const version = ++this.version;
    this.task?.cancel();
    this.textLayer?.cancel();
    const old = this.loading;
    this.document = undefined;
    if (old) void old.destroy().catch(() => {});
    this.controls(true);
    this.status(pdfCopy.chargementDuPdf);
    getElement('[data-pdf-error]', this.host).hidden = true;
    getElement('[data-pdf-retry]', this.host).hidden = true;
    getElement('[data-pdf-open]', this.host).hidden = true;
    getElement('.pdf-paper', this.host).hidden = true;
    getElement('.pdf-stage', this.host).scrollTo(0, 0);
    if (reference.accessUrl) {
      const controller = new AbortController();
      this.accessAbort = controller;
      try {
        const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal });
        if (!response.ok) throw new Error('Source access unavailable');
        const access = await response.json();
        if (this.disposed || version !== this.version) return;
        if (reference.highlightCitation && Array.isArray(access.locations))
          this.citationLocations = access.locations;
        const direct = new URL(access.url);
        if (!['http:', 'https:'].includes(direct.protocol) || direct.username || direct.password ||
            !/\/files\/[^/]/.test(direct.pathname) || !direct.searchParams.get('token')) throw new Error('Invalid source access');
        if (this.disposed || version !== this.version) return;
        url = direct;
      } catch {
        if (!this.disposed && version === this.version) this.fail(pdfCopy.impossibleDeChargerCePdfVerifiezLe);
        return;
      }
    }
    this.url = url;
    url.hash = '';
    const assets = '/pdfjs/';
    const task = getDocument({
      url: url.href,
      withCredentials: false,
      cMapUrl: `${assets}cmaps/`,
      cMapPacked: true,
      standardFontDataUrl: `${assets}standard_fonts/`,
      iccUrl: `${assets}iccs/`,
      wasmUrl: `${assets}wasm/`,
      maxImageSize: 16_000_000,
      canvasMaxAreaInBytes: 64_000_000,
    });
    this.loading = task;
    try {
      const document = await task.promise;
      if (this.disposed || version !== this.version) return;
      this.document = document;
      this.pageInput.max = String(document.numPages);
      getElement('[data-pdf-total]', this.host).textContent = formatText(pdfCopy.totalPages, {
        total: document.numPages,
      });
      const page = Number.isFinite(reference.page) ? Math.round(reference.page) : 1;
      const bounded = Math.min(document.numPages, Math.max(1, page));
      await this.render(bounded);
      if (
        !this.disposed &&
        this.document === document &&
        this.currentPage === bounded &&
        bounded !== page
      )
        this.status(
          formatText(pdfCopy.outOfRange, {
            page: bounded,
            total: document.numPages,
            requestedPage: page,
          }),
        );
    } catch (error) {
      if (!this.disposed && version === this.version) {
        if (await this.renew(error)) return;
        this.fail(
          error instanceof Error && error.name === 'PasswordException'
            ? pdfCopy.cePdfEstProtegeParUnMot
            : pdfCopy.impossibleDeChargerCePdfVerifiezLe,
        );
      }
    }
  }
  private async render(number: number): Promise<void> {
    const document = this.document;
    if (
      !document ||
      this.disposed ||
      !Number.isInteger(number) ||
      number < 1 ||
      number > document.numPages
    )
      return;
    const version = ++this.version;
    const previous = this.task;
    previous?.cancel();
    this.textLayer?.cancel();
    const changedPage = number !== this.currentPage;
    this.currentPage = number;
    if (this.reference) this.reference.page = number;
    this.pageInput.value = String(number);
    this.controls(true);
    this.status(formatText(pdfCopy.loadingPage, { page: number }));
    try {
      await previous?.promise.catch(() => {});
      const page = await document.getPage(number);
      if (this.disposed || version !== this.version) return;
      const stage = getElement('.pdf-stage', this.host);
      const original = page.getViewport({ scale: 1 });
      const padding = getComputedStyle(stage);
      const width = stage.clientWidth - parseFloat(padding.paddingLeft) - parseFloat(padding.paddingRight);
      const height = stage.clientHeight - parseFloat(padding.paddingTop) - parseFloat(padding.paddingBottom);
      const widthScale = width / original.width;
      const scale = Math.max(0.1, this.zoom === 'fit' ? widthScale
        : this.zoom === 'page' ? Math.min(widthScale, height / original.height) : Number(this.zoom));
      const viewport = page.getViewport({ scale });
      const ratio = Math.min(
        devicePixelRatio || 1,
        2,
        Math.sqrt(16_000_000 / (viewport.width * viewport.height)),
      );
      const paper = getElement('.pdf-paper', this.host);
      const canvas = getElement('canvas', paper);
      canvas.width = Math.ceil(viewport.width * ratio);
      canvas.height = Math.ceil(viewport.height * ratio);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      paper.style.width = `${viewport.width}px`;
      paper.style.height = `${viewport.height}px`;
      paper.style.setProperty('--total-scale-factor', String(scale * page.userUnit));
      const text = getElement('.pdf-text-layer', paper);
      text.replaceChildren();
      text.setAttribute(
        'aria-label',
        formatText(pdfCopy.pageAccessibleLabel, { title: this.title, page: number }),
      );
      this.task = page.render({
        canvas,
        viewport,
        transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
      });
      await this.task.promise;
      if (this.disposed || version !== this.version) return;
      this.textLayer = new TextLayer({
        textContentSource: page.streamTextContent(),
        container: text,
        viewport,
      });
      await this.textLayer.render();
      if (this.disposed || version !== this.version) return;
      paper.hidden = false;
      if (changedPage) stage.scrollTo(0, 0);
      this.renderCitation(paper, stage, number, original.width, original.height);
      this.status(
        formatText(pdfCopy.pageStatus, {
          page: number,
          total: document.numPages,
          note: text.textContent?.trim() ? '' : pdfCopy.aucunTexteSelectionnable,
        }),
      );
      getElement('[data-pdf-error]', this.host).hidden = true;
      getElement('[data-pdf-retry]', this.host).hidden = true;
      const open = getElement<HTMLAnchorElement>('[data-pdf-open]', this.host);
      open.href = `${this.url!.href}#page=${number}&zoom=page-width`;
      open.hidden = false;
      this.controls(false);
      this.renewed = false;
    } catch (error) {
      if (
        !this.disposed &&
        version === this.version &&
        !(error instanceof Error && error.name === 'RenderingCancelledException')
      ) {
        if (await this.renew(error)) return;
        this.fail(pdfCopy.impossibleDAfficherCettePageReessayez);
      }
    }
  }
  private renderCitation(paper: HTMLElement, stage: HTMLElement, page: number, width: number, height: number): void {
    const overlay = getElement('.pdf-citation-layer', paper);
    overlay.replaceChildren();
    if (!this.reference?.highlightCitation) return;
    const note = getElement('[data-pdf-citation]', this.host);
    note.hidden = this.citationLocations.length > 0;
    note.textContent = localeCopy.documentSearch.noPassageGeometry;

    for (const location of this.citationLocations) {
      if (location.page_no !== page) continue;
      const [left, top, right, bottom] = location.bbox;
      const bottomOrigin = location.coord_origin === 'BOTTOMLEFT';
      const x1 = Math.max(0, Math.min(left, right));
      const x2 = Math.min(width, Math.max(left, right));
      const y1 = Math.max(0, bottomOrigin ? height - Math.max(top, bottom) : Math.min(top, bottom));
      const y2 = Math.min(height, bottomOrigin ? height - Math.min(top, bottom) : Math.max(top, bottom));
      if (!(x2 > x1 && y2 > y1)) continue;
      const box = document.createElement('span');
      box.className = 'pdf-citation-box';
      box.style.left = `${100 * x1 / width}%`;
      box.style.top = `${100 * y1 / height}%`;
      box.style.width = `${100 * (x2 - x1) / width}%`;
      box.style.height = `${100 * (y2 - y1) / height}%`;
      overlay.append(box);
    }
    if (overlay.firstElementChild && this.revealedPage !== page) {
      const box = overlay.firstElementChild.getBoundingClientRect();
      const frame = stage.getBoundingClientRect();
      stage.scrollBy({ top: box.top - frame.top - frame.height / 3 });
    }
    this.revealedPage = page;
  }
  private async renew(error: unknown): Promise<boolean> {
    if (this.renewed || !this.reference?.accessUrl || !error || typeof error !== 'object' ||
        !('status' in error) || error.status !== 401) return false;
    this.renewed = true;
    await this.open({ ...this.reference, page: this.currentPage }, true);
    return true;
  }
  private fail(message: string): void {
    const error = getElement('[data-pdf-error]', this.host);
    error.textContent = message;
    error.hidden = false;
    getElement('[data-pdf-retry]', this.host).hidden = false;
    getElement('.pdf-paper', this.host).hidden = true;
    this.status(pdfCopy.pdfIndisponible);
    this.controls(false);
  }
  dispose(): void {
    this.disposed = true;
    this.accessAbort?.abort();
    ++this.version;
    this.observer.disconnect();
    clearTimeout(this.resizeTimer);
    this.task?.cancel();
    this.textLayer?.cancel();
    if (this.loading) void this.loading.destroy().catch(() => {});
    this.document = undefined;
  }
}
