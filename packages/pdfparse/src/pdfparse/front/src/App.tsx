import { InferenceTable } from "./Dialogs";
import { useEffect, useMemo, useRef, useState, lazy, Suspense } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Code2,
  Copy,
  Download,
  Image as ImageIcon,
  FileText,
  Folder,
  FolderOpen,
  Layers,
  ListFilter,
  ListTodo,
  LoaderCircle,
  LockKeyhole,
  Maximize2,
  Play,
  RefreshCw,
  Search,
  Settings2,
  Sparkles,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  getDocument,
  getResult,
  getRuntime,
  getSourceRoot,
  listDocuments,
  listJobs,
  processDocument,
  processFolder,
  stopFolderJob,
} from "./client/sdk.gen";
import { client } from "./client/client.gen";
import type {
  ChunkView,
  Document as DocumentInfo,
  Geometry,
  Item,
  Result,
  RuntimeStatus,
  Job,
} from "./client/types.gen";
import type { Highlight } from "./PdfViewer";
import { usePolling } from "./usePolling";
import {
  errorText,
  ObjectTree,
  FolderPicker,
  RootFolderPicker,
  SettingsModal,
  JobsModal,
  stageLabels,
} from "./Dialogs";
const PdfViewer = lazy(() =>
  import("./PdfViewer").then((module) => ({ default: module.PdfViewer })),
);

client.setConfig({ baseUrl: "", throwOnError: true });
const colors = [
  "#168a7a",
  "#5b72c5",
  "#cb8a2b",
  "#ad6098",
  "#448cad",
  "#b96d50",
];
const statuses = {
  idle: "À analyser",
  queued: "En attente",
  processing: "Analyse…",
  ready: "Analysé",
  error: "Erreur",
};

function ModelTags({ models }: { models?: Array<Record<string, unknown>> }) {
  const roles: Record<string, string> = { layout: "Layout", ocr: "OCR", vlm: "VLM", picture_classifier: "DFC", table_structure: "Table" };
  return <span className="model-tags">{(models || []).map((model, index) => (
    <span className="model-tag" key={`${model.role}-${model.name}-${index}`}
      title={[model.implementation, model.revision && `Révision : ${model.revision}`, model.device].filter(Boolean).join(" · ")}>
      {roles[String(model.role)] || String(model.role)} · {String(model.name)}
    </span>
  ))}</span>;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} Ko`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
}

function AssetImage({ url, label }: { url: string; label: string }) {
  const [failed, setFailed] = useState(false);
  return failed ? <p className="quiet-note">Image indisponible dans le stockage.</p> :
    <a href={url} target="_blank" rel="noreferrer" title="Ouvrir l’image originale">
      <img className="asset-image" src={url} alt={label} loading="lazy" decoding="async" onError={() => setFailed(true)} />
    </a>;
}
function isStructural(item: Item): boolean {
  return (
    [
      "group",
      "list_group",
      "inline_group",
      "field_item",
      "field_region",
    ].includes(item.type) &&
    ![
      item.content_for_llm,
      item.description,
      item.original_text,
      item.caption,
      item.ocr_text,
    ].some((text) => text?.trim())
  );
}
function Inspector({
  item,
  onSource,
}: {
  item: Item;
  onSource: (ref: string) => void;
}) {
  return (
    <div className="item-detail">
      <div className="detail-label">CONTENU ENRICHI</div>
      <p className="preserve-space">
        {item.content_for_llm ||
          item.description ||
            (item.original_text?.trim() ? item.original_text : null) ||
          (item.type === "picture"
            ? "Figure extraite sans contenu textuel. Vérifiez l’enrichissement OCR/VLM."
            : isStructural(item)
              ? "Conteneur documentaire sans texte propre."
              : "Texte non reconnu dans cette zone.")}
      </p>
        {!item.content_for_llm?.trim() && item.original_text?.trim() && (
          <p className="quiet-note">Texte reconnu, exclu du contenu indexé.</p>
        )}
      {item.original_text?.trim() && (
        <details>
          <summary>Texte original</summary>
          <pre>{item.original_text}</pre>
        </details>
      )}
      {item.ocr_text != null && (
        <details>
          <summary>Transcription OCR</summary>
          <pre>{item.ocr_text || "Aucun texte OCR retenu."}</pre>
        </details>
      )}
      <div className="detail-label">PROVENANCE</div>
      <dl>
        <dt>Classe source</dt>
        <dd>{item.source_class}</dd>
        <dt>Label Docling</dt>
        <dd>{item.label || "—"}</dd>
        <dt>Référence</dt>
        <dd>
          <code>{item.ref}</code>
        </dd>
      </dl>
      {item.parent_ref && (
        <button
          className="text-link"
          onClick={() => onSource(item.parent_ref!)}
        >
          Voir le parent <ChevronRight size={13} />
        </button>
      )}
      {isStructural(item) && item.children_refs.length > 0 && (
        <div className="structure-children">
          {item.children_refs.map((ref) => (
            <button
              key={ref}
              className="text-link"
              onClick={() => onSource(ref)}
            >
              Voir {ref} <ChevronRight size={13} />
            </button>
          ))}
        </div>
      )}
      {item.classification != null && (
        <details open>
          <summary>Classification et enrichissement</summary>
          {typeof item.classification === "object" &&
            !Array.isArray(item.classification) &&
            "route" in item.classification &&
            item.classification.route === "fallback" && (
              <p className="quiet-note">
                Classe DFC incertaine : repli OCR → VLM sur la figure entière.
              </p>
            )}
          <ObjectTree value={item.classification} />
        </details>
      )}
      <details>
        <summary>Géométries et données complètes</summary>
        <ObjectTree value={item.data} />
      </details>
    </div>
  );
}

export default function App() {
  const [folder, setFolder] = useState<string | null>(null);
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [documentPage, setDocumentPage] = useState(1);
  const [documentTotal, setDocumentTotal] = useState(0);
  const documentPageSize = 50;
  const documentRequest = useRef(0);
  const loadedDocumentFilter = useRef({folder: '', search: ''});
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [showStructure, setShowStructure] = useState(false);
  const [tab, setTab] = useState<"items" | "markdown" | "assets" | "chunks" | "raw">("items");
  const [result, setResult] = useState<Result | null>(null);
  const [comparison, setComparison] = useState<Pick<Result, "document_id" | "quality" | "metrics" | "manifest"> | null>(null);
  const [resultLoading, setResultLoading] = useState(false);
  const [resultRevision, setResultRevision] = useState(0);
  const [pageCount, setPageCount] = useState(0);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [selection, setSelection] = useState<string | null>(null);
  const itemCards = useRef(new Map<string, HTMLDivElement>());
  const [folderOpen, setFolderOpen] = useState(false);
  const [sourceRoot, setSourceRoot] = useState<string | null | undefined>(undefined);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [jobsOpen, setJobsOpen] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobsError, setJobsError] = useState("");
  const [submittingBatch, setSubmittingBatch] = useState(false);
  const activeJobs = jobs.filter(
    (job) => job.status === "running" || job.status === "queued",
  );
  const documentJob = activeJobs.find((job) => job.document_id === selected);
  const runtimeJob = activeJobs.find((job) => job.kind === "runtime");
  const batchJob = activeJobs.find((job) => job.kind === "batch");
  const batchRunning = !!batchJob;
  const [stoppingBatch, setStoppingBatch] = useState<string | null>(null);
  const wasBatchRunning = useRef(false);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const runtimeStatus = runtime?.status ?? "unloaded";
  const [figuresDisabled, setFiguresDisabled] = useState(false);
  const runtimeRevision = useRef<number | null>(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [allChunks, setAllChunks] = useState(true);
  const [rawMarkdown, setRawMarkdown] = useState(false);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    getSourceRoot({ signal: controller.signal })
      .then(({ data }) => {
        if (!controller.signal.aborted) setSourceRoot(data!.root);
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, []);
  const active = documents.find((doc) => doc.id === selected);
  const pdfUrl = selected ? `/api/documents/${selected}/pdf?v=${active?.version || ''}` : null;
  const rawResult = useMemo(() => tab === "raw" && result ? JSON.stringify({
    document_id: result.document_id, document_path: result.document_path,
    preparation: result.manifest, quality: result.quality, metrics: result.metrics,
    assets: result.assets, chunks: result.index_records,
  }, null, 2) : "", [tab, result]);
  const inProgress =
    active?.status === "processing" || active?.status === "queued";
  const runtimeUnavailable = ["loading", "unloading", "error"].includes(
    runtimeStatus,
  );
  const runtimeChanging = runtimeStatus === "loading" || runtimeStatus === "unloading";
  usePolling(async (signal) => {
    try {
      const { data } = await listJobs({ signal });
      if (!signal.aborted) {
        setJobs(data!);
        setJobsError("");
      }
    } catch (error) {
      if (!signal.aborted) setJobsError(errorText(error));
    }
  }, activeJobs.length > 0 || inProgress || runtimeChanging ? 5000 : 60000);

  function updateRuntime(data: RuntimeStatus) {
    setRuntime(data);
    setFiguresDisabled(
      (data.status === "unloaded" && !data.configuration?.picture_classifier) ||
        (data.status === "loaded" &&
          data.configuration !== null &&
          !data.configuration.picture_classifier),
    );
    if (
      runtimeRevision.current !== null &&
      runtimeRevision.current !== data.revision
    ) {
      setResult(null);
      setSelection(null);
      setDocuments((previous) =>
        previous.map((item) => ({ ...item, status: "idle", error: null })),
      );
    }
    runtimeRevision.current = data.revision;
  }
  usePolling(async (signal) => {
    try {
      const { data } = await getRuntime({ signal });
      if (data && !signal.aborted) updateRuntime(data);
    } catch {
      /* Document operations report connection errors independently. */
    }
  }, runtimeChanging || runtimeJob ? 10000 : 60000);
  useEffect(() => {
    if (folder !== null && runtime && !runtimeChanging) {
      void chooseFolder(folder, documentPage, query);
    }
  }, [runtime?.revision, runtime?.status]);
  useEffect(() => {
    if (wasBatchRunning.current && !batchRunning && folder !== null) {
      void chooseFolder(folder, documentPage, query);
    }
    wasBatchRunning.current = batchRunning;
  }, [batchRunning]);
  async function chooseFolder(path: string, targetPage = 1, search = query) {
    const request = ++documentRequest.current;
    setFolderOpen(false);
    setRefreshing(true);
    setError("");
    try {
      const { data } = await listDocuments({
        query: { folder: path, recursive: true, page: targetPage, page_size: documentPageSize, search },
      });
      if (request !== documentRequest.current) return;
      loadedDocumentFilter.current = {folder: path, search};
      setDocumentPage(data!.page);
      setDocumentTotal(data!.total);
      setFolder(path);
      setDocuments(data!.documents);
      setSelected((previous) =>
        data!.documents.some((doc) => doc.id === previous)
          ? previous
          : data!.documents[0]?.id || null,
      );
    } catch (err) {
      if (request === documentRequest.current) setError(errorText(err));
    } finally {
      if (request === documentRequest.current) setRefreshing(false);
    }
  }
  useEffect(() => {
    if (folder === null || (loadedDocumentFilter.current.folder === folder && loadedDocumentFilter.current.search === query)) return;
    const timer = window.setTimeout(() => void chooseFolder(folder, 1, query), 300);
    return () => window.clearTimeout(timer);
  }, [query, folder]);
  function updateDocument(document: DocumentInfo) {
    setDocuments((previous) =>
      previous.map((item) => (item.id === document.id ? document : item)),
    );
  }
  useEffect(() => {
    setResult(null);
    setResultLoading(false);
    setPageCount(0);
    setPage(1);
    setSelection(null);
    setZoom(1);
    setError("");
    setFilter("");
  }, [selected, active?.version]);
  usePolling(async (signal) => {
    if (!selected) return;
    try {
      const { data } = await getDocument({
        path: { document_id: selected }, signal,
      });
      if (!signal.aborted) updateDocument(data!);
    } catch (err) {
      if (!signal.aborted) setError(errorText(err));
    }
  }, selected && (inProgress || batchRunning) ? 5000 : null);
  useEffect(() => {
    if (!selected || active?.status !== "ready") return;
    const controller = new AbortController();
    setResultLoading(true);
    getResult({ path: { document_id: selected }, signal: controller.signal })
      .then(({ data }) => setResult(data!))
      .catch(async (err) => {
        if (controller.signal.aborted) return;
        setError(errorText(err));
        // Refresh stale list status when the server has evicted its cached result.
        try {
          const { data } = await getDocument({
            path: { document_id: selected },
            signal: controller.signal,
          });
          if (!controller.signal.aborted) updateDocument(data!);
        } catch {
          /* Keep the original result error visible. */
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setResultLoading(false);
      });
    return () => controller.abort();
  }, [selected, active?.status, active?.version, resultRevision]);
  async function process() {
    if (!selected) return;
    setError("");
    try {
      const { data } = await processDocument({
        path: { document_id: selected },
      });
      updateDocument(data!);
    } catch (err) {
      setError(errorText(err));
    }
  }
  async function processAll() {
    if (folder === null) return;
    setSubmittingBatch(true);
    setError("");
    try {
      const { data } = await processFolder({ query: { folder } });
      const job = data!;
      setJobs((previous) => previous.some((item) => item.id === job.id) ? previous : [job, ...previous]);
      setJobsOpen(true);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSubmittingBatch(false);
    }
  }
  async function stopBatch(jobId: string) {
    setStoppingBatch(jobId);
    setJobsError("");
    setError("");
    try {
      const job = await stopFolderJob({ path: { job_id: jobId }, throwOnError: true });
      setJobs((previous) => previous.map((item) => item.id === jobId ? job.data : item));
    } catch (err) {
      const message = errorText(err);
      setJobsError(message);
      setError(message);
    } finally {
      setStoppingBatch(null);
    }
  }
  function locate(id: string, locations: Geometry[]) {
    setSelection(id);
    if (locations[0]) setPage(locations[0].page_no);
  }
  function source(ref: string) {
    const item = result?.items.find((item) => item.ref === ref);
    if (item) {
      setTab("items");
      setFilter("");
      if (isStructural(item)) setShowStructure(true);
      locate(ref, item.locations);
    }
  }
  useEffect(() => {
    if (tab === "items" && selection) {
      itemCards.current.get(selection)?.scrollIntoView({ block: "nearest" });
    }
  }, [tab, selection, filter]);
  const selectedItem = result?.items.find((item) => item.ref === selection);
  const selectedChunk = result?.chunks.find((chunk) => chunk.id === selection);
  const highlights: Highlight[] =
    tab === "chunks"
      ? (result?.chunks || [])
          .filter((chunk) => allChunks || chunk.id === selection)
          .map((chunk, index) => ({
            id: chunk.id,
            color: colors[index % colors.length],
            locations: chunk.locations,
            selected: chunk.id === selection,
          }))
      : tab === "items" || tab === "assets"
        ? (result?.items || []).filter((item) => tab !== "assets" || item.type === "picture").map((item) => ({
            id: item.ref,
            label: `${item.label || item.type} · ${item.ref}`,
            color: colors[0],
            locations: item.locations,
            selected: item.ref === selection,
          }))
        : [];
  if (selection?.startsWith("extraction-")) {
    const region = result?.extraction_regions[Number(selection.slice("extraction-".length))];
    if (region && Array.isArray(region.bbox)) highlights.push({ id: selection, color: colors[0], selected: true,
      locations: [{ page_no: Number(region.page_no), bbox: region.bbox as [number, number, number, number], coord_origin: "TOPLEFT", charspan: null }] });
  }
  const selectionPages = [
    ...new Set(
      (selectedChunk?.locations || selectedItem?.locations || []).map(
        (loc) => loc.page_no,
      ),
    ),
  ];
  const visibleDocuments = useMemo(
    () => [...documents].sort((a, b) => a.size - b.size || a.path.localeCompare(b.path)),
    [documents]
  );
  const contentItems =
    result?.items.filter((item) => showStructure || !isStructural(item)) || [];
  const visibleItems = contentItems.filter((item) =>
    `${item.type} ${item.label || ""} ${item.content_for_llm || ""} ${item.original_text || ""}`
      .toLocaleLowerCase()
      .includes(filter.toLocaleLowerCase()),
  );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Layers size={23} />
          </div>
          <span>
            PdfParse<span className="brand-studio">STUDIO</span>
          </span>
        </div>
        <button
          className="settings-button"
          onClick={() => setSettingsOpen(true)}
        >
          <Settings2 size={17} />
          <span>Paramètres</span>
          <LockKeyhole size={13} />
        </button>
        <button className="settings-button" onClick={() => setJobsOpen(true)}>
          {activeJobs.length ? (
            <LoaderCircle className="spin" size={17} />
          ) : (
            <ListTodo size={17} />
          )}
          <span>
            Jobs
            {activeJobs.length > 0 &&
              ` · ${activeJobs.length} actif${activeJobs.length > 1 ? "s" : ""}`}
          </span>
        </button>
        {runtimeJob?.stage && (
          <p className="sidebar-job-step">
            {stageLabels[runtimeJob.stage]}
            {runtimeJob.detail && ` · ${runtimeJob.detail}`}
          </p>
        )}
        <div className="sidebar-section-title">
          BIBLIOTHÈQUE <span>{documentTotal || "—"}</span>
        </div>
        <button className="source-button" onClick={() => setFolderOpen(true)}>
          <FolderOpen size={18} />
          <span>
            <b>
              {folder === null
                ? "Choisir un dossier"
                : folder === "."
                  ? "Dossier source"
                  : folder.split("/").at(-1)}
            </b>
            <small>
              {folder === null
                ? "Vos PDF, au même endroit"
                : "Sous-dossiers inclus"}
            </small>
          </span>
          <ChevronDown size={15} />
        </button>
        <button
          className="secondary-button batch-button"
          disabled={batchJob ? batchJob.stop_requested || stoppingBatch === batchJob.id :
            folder === null || refreshing || (!query && documentTotal === 0) ||
            runtimeUnavailable || submittingBatch}
          onClick={() => void (batchJob ? stopBatch(batchJob.id) : processAll())}
        >
          {submittingBatch || batchJob?.stop_requested || stoppingBatch ? (
            <LoaderCircle className="spin" size={15} />
          ) : batchJob ? (
            <X size={15} />
          ) : (
            <Play size={15} />
          )}
          {batchJob?.stop_requested || stoppingBatch ? "Arrêt demandé" :
            batchJob ? "Arrêter l’extraction" : "Lancer l'extraction"}
        </button>
        <div className="search-box">
          <Search size={15} />
          <input
            aria-label="Rechercher un PDF"
            placeholder="Rechercher un PDF…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="file-list-heading">
          <span>DOCUMENTS</span>
          <button
            className="icon-button"
            aria-label="Actualiser les PDF"
            disabled={folder === null || refreshing}
            onClick={() => folder !== null && void chooseFolder(folder, documentPage)}
          >
            <RefreshCw size={13} className={refreshing ? "spin" : ""} />
          </button>
        </div>
        <nav className="file-list" aria-label="Documents PDF">
          {visibleDocuments.map((doc) => (
            <button
              key={doc.id}
              className={`file-button ${doc.id === selected ? "active" : ""}`}
              onClick={() => setSelected(doc.id)}
              title={doc.path}
            >
              <FileText size={18} />
              <span>
                <b>{doc.name}</b>
                <small>
                  {doc.path.includes("/") ? `${doc.path.slice(0, doc.path.lastIndexOf("/"))} · ` : ""}
                  {formatFileSize(doc.size)}
                </small>
              </span>
              <i
                className={`status-dot ${doc.status}`}
                title={statuses[doc.status]}
              />
            </button>
          ))}
          {!visibleDocuments.length && (
            <div className="sidebar-empty">
              <Folder size={26} />
              <p>
                {folder === null
                  ? "Sélectionnez votre dossier source."
                  : query
                    ? "Aucun PDF ne correspond."
                    : "Aucun PDF dans ce dossier."}
              </p>
              {folder === null && (
                <button
                  className="text-link"
                  onClick={() => setFolderOpen(true)}
                >
                  Ouvrir un dossier <ArrowRight size={13} />
                </button>
              )}
            </div>
          )}
        </nav>
        {folder !== null && (
          <div className="document-pagination" aria-label="Pagination des PDF">
            <button className="icon-button" aria-label="PDF précédents"
              disabled={refreshing || documentPage <= 1}
              onClick={() => void chooseFolder(folder, documentPage - 1)}><ArrowLeft size={16} /></button>
            <span>Page {documentPage} / {Math.max(1, Math.ceil(documentTotal / documentPageSize))}<small>{documentTotal} PDF</small></span>
            <button className="icon-button" aria-label="PDF suivants"
              disabled={refreshing || documentPage * documentPageSize >= documentTotal}
              onClick={() => void chooseFolder(folder, documentPage + 1)}><ArrowRight size={16} /></button>
          </div>
        )}
        <footer className="sidebar-footer">
          <span className="connection-dot" /> Espace de travail local{" "}
          <LockKeyhole size={12} />
        </footer>
      </aside>
      <main className="workspace">
        <header className="workspace-header">
          <div className="breadcrumb">
            <FolderOpen size={15} />
            <span>Documents</span>
            <ChevronRight size={14} />
            <strong>{active?.name || "Votre espace documentaire"}</strong>
          </div>
          <span className="local-badge">LOCAL</span>
        </header>
        <div className="document-heading">
          <div>
            <div className="eyebrow">ATELIER DOCUMENTAIRE</div>
            <h1>{active?.name || "Explorez vos documents"}</h1>
            <p>
              {active ? (
                <>
                  <span className={`status-dot ${active.status}`} />
                  {statuses[active.status]}
                  {documentJob?.stage && (
                    <>
                      <span className="dot-separator">·</span>
                      {stageLabels[documentJob.stage]}
                    </>
                  )}
                  <span className="dot-separator">·</span>
                  {pageCount
                    ? `${pageCount} page${pageCount > 1 ? "s" : ""}`
                    : "PDF"}
                  <span className="dot-separator">·</span>Sources traçables
                </>
              ) : (
                "Du PDF aux éléments enrichis, avec toute leur provenance."
              )}
            </p>
          </div>
          {active && (
            <button
              className="primary-button"
              disabled={
                inProgress || active.status === "ready" || runtimeUnavailable
              }
              onClick={() => void process()}
            >
              {inProgress ? (
                <LoaderCircle className="spin" size={16} />
              ) : active.status === "ready" ? (
                <Check size={16} />
              ) : (
                <Play size={15} />
              )}{" "}
              {runtimeUnavailable
                ? "Configurer les modèles"
                : inProgress
                  ? "Analyse en cours"
                  : active.status === "ready"
                    ? "Analyse terminée"
                    : "Analyser le document"}
            </button>
          )}
        </div>
        {figuresDisabled && (
          <div className="figure-setup-banner">
            <div>
              <strong>Figures sans OCR/VLM</strong>
              <p>
                Pour extraire leur texte, choisissez un profil dans Paramètres
                puis cliquez sur « Charger et appliquer » avant de relancer
                l’analyse.
              </p>
            </div>
            <button
              className="secondary-button"
              onClick={() => setSettingsOpen(true)}
            >
              Configurer les figures
            </button>
          </div>
        )}
        {(error || active?.error) && (
          <div className="error-banner workspace-error" role="alert">
            {error || active?.error}
            <button
              className="icon-button"
              aria-label="Masquer l’erreur"
              onClick={() => setError("")}
            >
              <X size={15} />
            </button>
          </div>
        )}
        <div className="document-content">
          <section className="viewer-panel">
            <div className="viewer-toolbar">
              <div className="page-controls">
                <button
                  className="icon-button"
                  aria-label="Page précédente"
                  disabled={page <= 1}
                  onClick={() => setPage((value) => value - 1)}
                >
                  <ArrowLeft size={15} />
                </button>
                <label>
                  Page{" "}
                  <input
                    aria-label="Numéro de page"
                    type="number"
                    min={1}
                    max={pageCount || 1}
                    value={page}
                    onChange={(e) =>
                      setPage(
                        Math.min(
                          pageCount || 1,
                          Math.max(1, Number(e.target.value) || 1),
                        ),
                      )
                    }
                  />
                  <span>/ {pageCount || "—"}</span>
                </label>
                <button
                  className="icon-button"
                  aria-label="Page suivante"
                  disabled={page >= pageCount}
                  onClick={() => setPage((value) => value + 1)}
                >
                  <ArrowRight size={15} />
                </button>
              </div>
              <div className="zoom-controls">
                <button
                  className="icon-button"
                  aria-label="Réduire"
                  disabled={zoom <= 0.6}
                  onClick={() => setZoom((value) => value - 0.2)}
                >
                  <ZoomOut size={16} />
                </button>
                <span>{Math.round(zoom * 100)} %</span>
                <button
                  className="icon-button"
                  aria-label="Agrandir"
                  disabled={zoom >= 2}
                  onClick={() => setZoom((value) => value + 0.2)}
                >
                  <ZoomIn size={16} />
                </button>
                <button
                  className="icon-button"
                  aria-label="Réinitialiser le zoom"
                  onClick={() => setZoom(1)}
                >
                  <Maximize2 size={15} />
                </button>
              </div>
            </div>
            {selectionPages.length > 1 && (
              <div className="page-links">
                Sources sur les pages{" "}
                {selectionPages.map((number) => (
                  <button
                    key={number}
                    className={page === number ? "selected" : ""}
                    onClick={() => setPage(number)}
                  >
                    {number}
                  </button>
                ))}
              </div>
            )}
            <Suspense
              fallback={
                <div className="viewer-empty">Chargement du lecteur PDF…</div>
              }
            >
              <PdfViewer
                key={`${selected}:${active?.version || ''}`}
                url={pdfUrl}
                onPageCount={setPageCount}
                onPageChange={setPage}
                page={page}
                zoom={zoom}
                highlights={highlights}
                onHighlight={(id) => {
                  if (tab === "items") setFilter("");
                  setSelection(id);
                }}
              />
            </Suspense>
            <footer className="viewer-footer">
              <span>
                <span className="connection-dot" />
                {tab === "chunks"
                  ? "Zones des éléments sources des chunks"
                  : tab === "items"
                    ? "Éléments détectés · cliquez sur une zone"
                    : "Document original"}
              </span>
              <span>
                PDF · {active ? "Lecture seule" : "Aucun document sélectionné"}
              </span>
            </footer>
          </section>
          <aside className="inspector-panel">
            <div className="inspector-title">
              <Sparkles size={17} />
              <h2>Comprendre le document</h2>
            </div>
            <div
              className="tabs"
              role="tablist"
              aria-label="Résultats du document"
            >
              {(
                [
                  ["items", "Éléments", Layers],
                  ["markdown", "Markdown", BookOpen],
                  ["assets", "Assets", ImageIcon],
                  ["chunks", "Chunks", Code2],
                  ["raw", "Brut", FileText],
                ] as const
              ).map(([key, label, Icon]) => (
                <button
                  key={key}
                  role="tab"
                  aria-selected={tab === key}
                  onClick={() => {
                    setTab(key);
                    setSelection(null);
                  }}
                >
                  <Icon size={15} />
                  {label}
                  {result && (
                    <small>
                      {key === "items"
                        ? contentItems.length
                        : key === "chunks"
                          ? result.chunks.length
                          : key === "assets" ? (result.assets || []).length
                          : ""}
                    </small>
                  )}
                </button>
              ))}
            </div>
            {!result ? (
              <div className="inspector-empty">
                <div className="empty-icon">
                  <Sparkles size={25} />
                </div>
                <h3>
                  {resultLoading
                    ? "Chargement des résultats…"
                    : active?.status === "ready"
                      ? "Résultats indisponibles"
                      : inProgress
                        ? "Lecture du document…"
                        : "Le contenu prend forme ici"}
                </h3>
                <p>
                  {resultLoading
                    ? "Lecture du document enrichi et de ses chunks."
                    : active?.status === "ready"
                      ? "Réessayez de charger les résultats sans relancer l’analyse."
                      : inProgress
                        ? "Extraction, enrichissement et découpage. Vous pouvez continuer à parcourir le PDF."
                        : "Lancez l’analyse pour explorer les éléments, le Markdown et les chunks du document."}
                </p>
                {(inProgress || resultLoading) && (
                  <LoaderCircle className="spin" size={21} />
                )}
                {active?.status === "ready" && !resultLoading && (
                  <button
                    className="primary-button"
                    onClick={() => setResultRevision((value) => value + 1)}
                  >
                    Recharger les résultats
                  </button>
                )}
                <div className="empty-steps">
                  <span>
                    <Layers size={15} /> Éléments et provenance
                  </span>
                  <span>
                    <BookOpen size={15} /> Document en Markdown
                  </span>
                  <span>
                    <Code2 size={15} /> Chunks reliés au PDF
                  </span>
                </div>
              </div>
            ) : (
              <>
                <details className="result-notices">
                  <summary>Qualité et préparation RAG</summary>
                  <button onClick={() => setComparison({ document_id: result.document_id, quality: result.quality, metrics: result.metrics, manifest: result.manifest })}>Mémoriser comme référence de comparaison</button>
                  {comparison?.document_id === result.document_id && <details><summary>Comparaison avec la référence mémorisée</summary>
                    <table><thead><tr><th>Mesure</th><th>Référence</th><th>Actuel</th></tr></thead><tbody>
                    {[...Object.keys(result.quality), ...Object.keys(result.metrics)].map((key) => <tr key={key}><td>{key}</td><td>{JSON.stringify(comparison.quality[key] ?? comparison.metrics[key])}</td><td>{JSON.stringify(result.quality[key] ?? result.metrics[key])}</td></tr>)}
                    </tbody></table><p>Manifeste de référence : {String(comparison.manifest.manifest_hash ?? "indisponible")}</p>
                  </details>}
                  <p>Couverture du texte extrait : {typeof result.quality.coverage === "number" ? `${(100 * result.quality.coverage).toFixed(1)} %` : "inconnue"}</p>
                  <p>Les zones non détectées par le layout ne sont pas mesurées.</p>
                  {result.extraction_regions.map((region, index) => Array.isArray(region.source_refs) && !region.source_refs.length && Array.isArray(region.bbox) ? <button key={index} onClick={() => locate(`extraction-${index}`, [{ page_no: Number(region.page_no), bbox: region.bbox as [number, number, number, number], coord_origin: "TOPLEFT", charspan: null }])}>Zone sans source · page {Number(region.page_no)}</button> : null)}
                  {["unindexed_refs", "suspicious_text_refs", "repetitive_text_refs", "extracted_but_unindexed_refs"].map((kind) => (
                    <div key={kind}><strong>{kind}</strong>{Array.isArray(result.quality[kind]) && result.quality[kind].map((ref) => typeof ref === "string" ? (
                      <button key={ref} onClick={() => source(ref)}>{ref}</button>
                    ) : null)}</div>
                  ))}
                  <InferenceTable calls={Array.isArray(result.metrics.inferences) ? result.metrics.inferences as Record<string, unknown>[] : []}/>
                  <details><summary>Métriques brutes (incluent celles de la préparation si résultat en cache)</summary><pre>{JSON.stringify(result.metrics, null, 2)}</pre></details>
                  <button onClick={() => {
                    const blob = new Blob([result.index_records.map((record) => JSON.stringify(record)).join("\n") + "\n"], { type: "application/x-ndjson" });
                    const url = URL.createObjectURL(blob); const link = document.createElement("a");
                    link.href = url; link.download = `${result.document_id}.jsonl`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
                  }}>Exporter JSONL</button>
                  <button onClick={() => {
                    const url = URL.createObjectURL(new Blob([JSON.stringify({ manifest: result.manifest, quality: result.quality, metrics: result.metrics }, null, 2)], { type: "application/json" }));
                    const link = document.createElement("a"); link.href = url; link.download = `${result.document_id}.preparation.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
                  }}>Exporter le rapport de préparation</button>
                </details>
                {!!result.notices?.length && (
                  <div className="result-notices" role="status">
                    {result.notices.map((notice) => (
                      <p key={notice}>{notice}</p>
                    ))}
                  </div>
                )}
                {tab === "items" && (
                  <>
                    <div className="inspector-filter">
                      <Search size={14} />
                      <input
                        aria-label="Filtrer les éléments"
                        placeholder="Type, label ou contenu…"
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                      />
                      <ListFilter size={15} />
                    </div>
                    <label className="structure-toggle">
                      <input
                        type="checkbox"
                        checked={showStructure}
                        onChange={(event) => {
                          setShowStructure(event.target.checked);
                          if (
                            !event.target.checked &&
                            selectedItem &&
                            isStructural(selectedItem)
                          )
                            setSelection(null);
                        }}
                      />{" "}
                      Afficher la structure
                    </label>
                    <div className="inspector-scroll" role="tabpanel">
                      {visibleItems.map((item) => (
                        <div
                          key={item.ref}
                          data-element-ref={item.ref}
                          ref={(node) => {
                            if (node) itemCards.current.set(item.ref, node);
                            else itemCards.current.delete(item.ref);
                          }}
                          className={`item-card ${selection === item.ref ? "selected" : ""}`}
                        >
                          <button
                            className="item-summary"
                            onClick={() => locate(item.ref, item.locations)}
                          >
                            <span className="type-icon">
                              <FileText size={15} />
                            </span>
                            <span>
                              <b>{item.label || item.type}</b>
                              <small>
                                {(
                                  item.content_for_llm ||
                                  (item.original_text?.trim() ? item.original_text : null) ||
                                  item.caption ||
                                  (isStructural(item)
                                    ? `${item.children_refs.length} élément(s) enfant(s)`
                                    : "Texte non reconnu")
                                ).slice(0, 95)}
                              </small>
                              <ModelTags models={item.models} />
                            </span>
                            <span className="page-badge">
                              {item.locations[0]
                                ? `p. ${item.locations[0].page_no}`
                                : "Structure"}
                            </span>
                          </button>
                          {selection === item.ref && (
                            <Inspector item={item} onSource={source} />
                          )}
                        </div>
                      ))}
                      {!visibleItems.length && (
                        <p className="quiet-note">
                          Aucun élément correspondant.
                        </p>
                      )}
                    </div>
                  </>
                )}
                {tab === "markdown" && (
                  <>
                    <div className="markdown-tools">
                      <div className="segmented">
                        <button
                          className={!rawMarkdown ? "selected" : ""}
                          onClick={() => setRawMarkdown(false)}
                        >
                          Aperçu
                        </button>
                        <button
                          className={rawMarkdown ? "selected" : ""}
                          onClick={() => setRawMarkdown(true)}
                        >
                          Source
                        </button>
                      </div>
                      <button
                        className="icon-button"
                        aria-label="Copier le Markdown"
                        onClick={() => {
                          void navigator.clipboard
                            .writeText(result.markdown)
                            .then(() => {
                              setCopied(true);
                              window.setTimeout(() => setCopied(false), 1800);
                            })
                            .catch((err) => setError(errorText(err)));
                        }}
                      >
                        {copied ? <Check size={15} /> : <Copy size={15} />}
                      </button>
                    </div>
                    <div
                      className="inspector-scroll markdown-content"
                      role="tabpanel"
                    >
                      {rawMarkdown ? (
                        <pre>{result.markdown}</pre>
                      ) : (
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {result.markdown}
                        </ReactMarkdown>
                      )}
                      {!result.markdown && (
                        <p className="quiet-note">
                          Aucun texte extrait de ce document.
                        </p>
                      )}
                    </div>
                  </>
                )}
                {tab === "assets" && (
                  <div className="inspector-scroll assets-panel" role="tabpanel" aria-label="Assets extraits">
                    {(result.assets || []).map((asset, index) => <article className="asset-card" key={asset.id}>
                      <button className="asset-title" onClick={() => locate(asset.ref, asset.locations)}>
                        <ImageIcon size={16} /> Image {index + 1}
                        <span className="page-badge">p. {[...new Set(asset.locations.map((loc) => loc.page_no))].join(", ") || "—"}</span>
                      </button>
                      <ModelTags models={asset.models} />
                      {asset.available && asset.url ? <AssetImage key={asset.id} url={asset.url} label={asset.caption || `Image extraite ${index + 1}`} /> :
                        <p className="quiet-note">Image extraite sans fichier conservé.</p>}
                      {asset.caption && <b>{asset.caption}</b>}
                      {asset.description && <p className="preserve-space">{asset.description}</p>}
                      <small>{asset.chunk_ids.length} chunk(s) associé(s)</small>
                      <button className="source-ref" onClick={() => source(asset.ref)}>Voir l’élément source <ChevronRight size={12} /></button>
                      <details><summary>Métadonnées de l’image</summary><pre>{JSON.stringify(asset, null, 2)}</pre></details>
                    </article>)}
                    {!result.assets?.length && <p className="quiet-note">Aucune image extraite dans ce document.</p>}
                  </div>
                )}
                {tab === "raw" && (
                  <>
                    <div className="raw-actions">
                      <span>{result.index_records.length} chunks · JSON</span>
                      <button aria-label="Copier le JSON" onClick={() => navigator.clipboard.writeText(rawResult).catch((error) => setError(errorText(error)))}><Copy size={15} /> Copier</button>
                      <button aria-label="Télécharger le JSON" onClick={() => {
                        const url = URL.createObjectURL(new Blob([rawResult], { type: "application/json" }));
                        const link = document.createElement("a"); link.href = url;
                        link.download = `${active?.name || "document"}.chunks.json`; link.click();
                        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
                      }}><Download size={15} /> JSON</button>
                    </div>
                    <div className="inspector-scroll raw-panel" role="tabpanel" aria-label="Chunks JSON brut"><pre>{rawResult}</pre></div>
                  </>
                )}
                {tab === "chunks" && (
                  <>
                    <div className="chunks-options">
                      <label>
                        <input
                          type="checkbox"
                          checked={allChunks}
                          onChange={(e) => setAllChunks(e.target.checked)}
                        />{" "}
                        Afficher toutes les zones
                      </label>
                      <span>{result.chunks.length} chunks</span>
                    </div>
                    <div className="inspector-scroll" role="tabpanel">
                      {result.chunks.map((chunk: ChunkView, index) => (
                        <div
                          key={chunk.id}
                          className={`chunk-card ${selection === chunk.id ? "selected" : ""}`}
                          style={
                            {
                              "--chunk-color": colors[index % colors.length],
                            } as React.CSSProperties
                          }
                        >
                          <button
                            className="chunk-summary"
                            onClick={() => locate(chunk.id, chunk.locations)}
                          >
                            <span className="chunk-title">
                              <i />
                              Chunk {String(index + 1).padStart(2, "0")}
                              <small>{chunk.text.length} caractères</small>
                            </span>
                            {chunk.context && (
                              <span className="chunk-context">
                                {chunk.context.replaceAll("\n", " › ")}
                              </span>
                            )}
                            <span className="chunk-text">{chunk.text}</span>
                            <span className="chunk-footer">
                              {chunk.source_refs.length} sources · pages{" "}
                              {[
                                ...new Set(
                                  chunk.locations.map((loc) => loc.page_no),
                                ),
                              ].join(", ") || "—"}
                            </span>
                          </button>
                          {selection === chunk.id && (
                            <div className="chunk-detail">
                              <div className="detail-label">SOURCES</div>
                              {chunk.source_refs.map((ref) => (
                                <button
                                  className="source-ref"
                                  key={ref}
                                  onClick={() => source(ref)}
                                >
                                  {ref}
                                  <ChevronRight size={12} />
                                </button>
                              ))}
                              <details>
                                <summary>Transformation</summary>
                                <code>{chunk.transformation_id}</code>
                              </details>
                            </div>
                          )}
                        </div>
                      ))}
                      {!result.chunks.length && (
                        <p className="quiet-note">
                          Aucun contenu indexable dans ce document.
                        </p>
                      )}
                    </div>
                  </>
                )}
              </>
            )}
            <footer className="inspector-footer">
              <LockKeyhole size={12} /> Les données originales sont conservées.
            </footer>
          </aside>
        </div>
      </main>
      {sourceRoot === null && (
        <RootFolderPicker onChoose={(root) => {
          setSourceRoot(root);
          void chooseFolder(".");
        }} />
      )}
      {folderOpen && sourceRoot && (
        <FolderPicker
          current={folder || ""}
          onChoose={(path) => void chooseFolder(path)}
          onClose={() => setFolderOpen(false)}
        />
      )}
      {settingsOpen && (
        <SettingsModal
          runtime={runtime}
          onRuntimeChange={updateRuntime}
          runtimeJob={runtimeJob}
          onClose={() => setSettingsOpen(false)}
        />
      )}
      {jobsOpen && (
        <JobsModal
          jobs={jobs}
          error={jobsError}
          stoppingBatch={stoppingBatch}
          onStop={(id) => void stopBatch(id)}
          onClose={() => setJobsOpen(false)}
        />
      )}
    </div>
  );
}
