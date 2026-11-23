import localeCopy from '../../content/fr.json';
const {common:commonCopy, pdf:pdfCopy}=localeCopy;
import { escapeHtml } from '../ui/html';
import { formatText } from '../../content/text';
import { getElement } from '../ui/dom';
import { getDocument, GlobalWorkerOptions, TextLayer } from 'pdfjs-dist';
import type { PDFDocumentLoadingTask, PDFDocumentProxy, PDFPageProxy, RenderTask } from 'pdfjs-dist';
import { icon } from '../ui/icons/icon';
GlobalWorkerOptions.workerSrc = '/pdfjs/pdf.worker.min.mjs';

export interface PdfDocumentReference {
  url: string;
  page: number;
  accessUrl?: string;
  onRefresh?: () => Promise<string | null>;
  highlightCitation?: boolean;
  continuous?: boolean;
  focusPassageId?: string;
  onPassageSelect?: (id: string) => void;
}

interface PassageLocation {
  page_no: number;
  bbox: [number, number, number, number];
  coord_origin: 'TOPLEFT' | 'BOTTOMLEFT';
  passageId?: string;
}
interface WindowPage {
  paper?: HTMLElement;
  page?: PDFPageProxy;
  task?: RenderTask;
  text?: TextLayer;
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
  private revealPassage = false;
  private url?: URL;
  private zoom = 'fit';
  private disposed = false;
  private resizeTimer?: ReturnType<typeof setTimeout>;
  private hostWidth = 0;
  private hostHeight = 0;
  private slots: HTMLDivElement[] = [];
  private visiblePages = new Map<number, WindowPage>();
  private scrollFrame = 0;
  private estimate = 0;
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
      void this.resize();
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
    this.stage.addEventListener('scroll', () => {
      if (!this.slots.length || this.scrollFrame) return;
      this.scrollFrame = requestAnimationFrame(() => {
        this.scrollFrame = 0;
        this.followScroll();
      });
    }, { passive: true });
    this.observer = new ResizeObserver((entries) => {
      const width = Math.round(entries[0]?.contentRect.width ?? 0);
      const height = Math.round(entries[0]?.contentRect.height ?? 0);
      if (width === this.hostWidth && (this.reference?.continuous || height === this.hostHeight)) return;
      this.hostWidth = width;
      this.hostHeight = height;
      clearTimeout(this.resizeTimer);
      if (width && height && this.document && (this.zoom === 'fit' || this.zoom === 'page'))
        this.resizeTimer = setTimeout(() => {
          void this.resize();
        }, 120);
    });
    this.observer.observe(host);
    this.controls(true);
  }
  private get stage(): HTMLElement {
    return getElement('.pdf-stage', this.host);
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
    this.clearWindow();
    this.revealedPage = undefined;
    this.revealPassage = Boolean(reference.focusPassageId && !renewing);
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
      disableAutoFetch: reference.continuous,
      disableStream: reference.continuous,
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
      if (reference.continuous) await this.buildWindow(bounded);
      else await this.render(bounded);
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
    if (this.slots.length) {
      this.stage.scrollTop = this.slots[number - 1]!.offsetTop;
      this.updateWindow(number);
      return;
    }
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
      const scale = this.pageScale(page);
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
  private pageScale(page: PDFPageProxy): number {
    const original = page.getViewport({ scale: 1 });
    const padding = getComputedStyle(this.stage);
    const width = this.stage.clientWidth - parseFloat(padding.paddingLeft) - parseFloat(padding.paddingRight);
    const height = this.stage.clientHeight - parseFloat(padding.paddingTop) - parseFloat(padding.paddingBottom);
    const fit = width / original.width;
    return Math.max(.1, this.zoom === 'fit' ? fit
      : this.zoom === 'page' ? Math.min(fit, height / original.height) : Number(this.zoom));
  }

  private async resize(): Promise<void> {
    if (!this.reference?.continuous) return this.render(this.currentPage);
    await this.buildWindow(this.currentPage);
  }

  private clearWindow(): void {
    cancelAnimationFrame(this.scrollFrame);
    this.scrollFrame = 0;
    for (const number of this.visiblePages.keys()) this.releasePage(number);
    this.slots.forEach((slot) => slot.remove());
    this.slots = [];
    delete this.stage.dataset.continuous;
  }

  private async buildWindow(number: number): Promise<void> {
    const document = this.document;
    if (!document) return;
    const version = ++this.version;
    try {
      const page = await document.getPage(number);
      if (this.disposed || this.version !== version) return;
      this.clearWindow();
      this.stage.dataset.continuous = 'true';
      this.estimate = page.getViewport({ scale: this.pageScale(page) }).height;
      const fragment = globalThis.document.createDocumentFragment();
      this.slots = Array.from({ length: document.numPages }, () => {
        const slot = globalThis.document.createElement('div');
        slot.className = 'pdf-page-slot';
        slot.style.minHeight = `${this.estimate}px`;
        fragment.append(slot);
        return slot;
      });
      this.stage.append(fragment);
      this.stage.scrollTop = this.slots[number - 1]!.offsetTop;
      this.updateWindow(number);
    } catch (error) {
      if (this.disposed || version !== this.version || await this.renew(error)) return;
      this.fail(pdfCopy.impossibleDAfficherCettePageReessayez);
    }
  }

  private followScroll(): void {
    if (!this.slots.length) return;
    const position = this.stage.scrollTop + Math.min(this.stage.clientHeight / 2, this.estimate / 2);
    let low = 0;
    let high = this.slots.length - 1;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      if (this.slots[middle]!.offsetTop <= position) low = middle;
      else high = middle - 1;
    }
    if (low + 1 !== this.currentPage) this.updateWindow(low + 1);
  }

  private releasePage(number: number): void {
    const page = this.visiblePages.get(number);
    if (!page) return;
    this.visiblePages.delete(number);
    page.task?.cancel();
    page.text?.cancel();
    page.paper?.remove();
    page.page?.cleanup();
  }

  private updateWindow(number: number): void {
    if (!this.document) return;
    const from = Math.max(1, number - 1);
    const to = Math.min(this.document.numPages, number + 1);
    for (const page of this.visiblePages.keys()) {
      if (page < from || page > to) this.releasePage(page);
    }
    this.currentPage = number;
    if (this.reference) this.reference.page = number;
    this.pageInput.value = String(number);
    const existing = this.visiblePages.get(number);
    this.controls(!existing?.paper);
    this.status(formatText(pdfCopy.pageStatus, { page: number, total: this.document.numPages, note: '' }));
    const open = getElement<HTMLAnchorElement>('[data-pdf-open]', this.host);
    open.href = `${this.url!.href}#page=${number}&zoom=page-width`;
    open.hidden = false;
    for (const page of [number, number - 1, number + 1]) {
      if (page < from || page > to || this.visiblePages.has(page)) continue;
      const entry: WindowPage = {};
      this.visiblePages.set(page, entry);
      void this.paintWindowPage(page, entry);
    }
  }

  private async paintWindowPage(number: number, entry: WindowPage): Promise<void> {
    const document = this.document!;
    const active = () => this.visiblePages.get(number) === entry && !this.disposed;
    try {
      const page = await document.getPage(number);
      if (!active()) return;
      entry.page = page;
      const original = page.getViewport({ scale: 1 });
      const scale = this.pageScale(page);
      const viewport = page.getViewport({ scale });
      const ratio = Math.min(devicePixelRatio || 1, 2, Math.sqrt(8_000_000 / (viewport.width * viewport.height)));
      const paper = getElement<HTMLElement>('.pdf-paper', this.host).cloneNode(true) as HTMLElement;
      const canvas = getElement<HTMLCanvasElement>('canvas', paper);
      const text = getElement<HTMLElement>('.pdf-text-layer', paper);
      canvas.width = Math.ceil(viewport.width * ratio);
      canvas.height = Math.ceil(viewport.height * ratio);
      canvas.style.width = paper.style.width = `${viewport.width}px`;
      canvas.style.height = paper.style.height = `${viewport.height}px`;
      paper.style.setProperty('--total-scale-factor', String(scale * page.userUnit));
      text.setAttribute('aria-label', formatText(pdfCopy.pageAccessibleLabel, { title: this.title, page: number }));
      entry.task = page.render({ canvas, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] });
      await entry.task.promise;
      if (!active()) return;
      entry.text = new TextLayer({ textContentSource: page.streamTextContent(), container: text, viewport });
      await entry.text.render();
      if (!active()) return;
      const slot = this.slots[number - 1]!;
      slot.style.minHeight = `${viewport.height}px`;
      slot.append(paper);
      entry.paper = paper;
      paper.hidden = false;
      this.renderCitation(paper, this.stage, number, original.width, original.height);
      if (number === this.currentPage) {
        this.controls(false);
        this.renewed = false;
      }
    } catch (error) {
      if (!active() || number !== this.currentPage || await this.renew(error)) return;
      this.fail(pdfCopy.impossibleDAfficherCettePageReessayez);
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
      const select = location.passageId && this.reference.onPassageSelect;
      const box = select ? document.createElement('button') : document.createElement('span');
      if (select && box instanceof HTMLButtonElement) {
        overlay.removeAttribute('aria-hidden');
        box.type = 'button';
        box.setAttribute('aria-label', localeCopy.documentSearch.indexedPassage);
        box.addEventListener('click', () => select(location.passageId!));
      }
      box.className = 'pdf-citation-box';
      if (location.passageId && location.passageId === this.reference.focusPassageId) box.dataset.selected = 'true';
      box.style.left = `${100 * x1 / width}%`;
      box.style.top = `${100 * y1 / height}%`;
      box.style.width = `${100 * (x2 - x1) / width}%`;
      box.style.height = `${100 * (y2 - y1) / height}%`;
      overlay.append(box);
    }
    const selected = this.revealPassage && page === this.currentPage
      ? overlay.querySelector('[data-selected="true"]') : null;
    const visibleBox = selected ?? (!this.reference.continuous && this.revealedPage !== page ? overlay.firstElementChild : null);
    if (visibleBox) {
      this.revealPassage = false;
      const box = visibleBox.getBoundingClientRect();
      const frame = stage.getBoundingClientRect();
      if (box.top < frame.top || box.bottom > frame.bottom)
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
    this.clearWindow();
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
    this.clearWindow();
    clearTimeout(this.resizeTimer);
    this.task?.cancel();
    this.textLayer?.cancel();
    if (this.loading) void this.loading.destroy().catch(() => {});
    this.document = undefined;
  }
}
