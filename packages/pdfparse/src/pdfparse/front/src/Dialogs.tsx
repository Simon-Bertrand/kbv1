import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  Check,
  ChevronRight,
  Folder,
  FolderOpen,
  LoaderCircle,
  Sparkles,
  X,
} from "lucide-react";
import {
  getSettings,
  listFolders,
  getRuntimeOptions,
  loadRuntime,
  unloadRuntime,
  selectSourceRoot,
} from "./client/sdk.gen";
import type {
  Job,
  FolderListing,
  Settings,
  RuntimeOptions,
  RuntimeStatus,
  RuntimeConfigurationInput,
  ModelSelectionInput,
  ModelCategory,
} from "./client/types.gen";

export function errorText(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    if (Array.isArray(error.detail))
      return error.detail
        .map(
          (item) => `${item.loc?.join(".") || "Configuration"} : ${item.msg}`,
        )
        .join(" ; ");
    return String(error.detail);
  }
  return error instanceof Error
    ? error.message
    : "La requête a échoué. Vérifiez la connexion au serveur.";
}
function Modal({
  title,
  subtitle,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  onClose?: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    return () => ref.current?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={`modal ${wide ? "wide" : ""}`}
      onCancel={(event) => {
        event.preventDefault();
        onClose?.();
      }}
      onClick={(event) => {
        if (event.target === ref.current) onClose?.();
      }}
    >
      <header className="modal-header">
        <div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        {onClose && (
          <button className="icon-button" aria-label="Fermer" onClick={onClose}>
            <X size={20} />
          </button>
        )}
      </header>
      {children}
    </dialog>
  );
}

export const stageLabels = {
  extraction: "Extraction et layout",
  enrichment: "Enrichissement",
  classification: "Classification des figures",
  ocr: "OCR",
  vlm: "Description VLM",
  chunking: "Découpage des chunks",
  rendering: "Markdown et résultats",
  unloading: "Libération des modèles",
  loading: "Chargement des modèles",
  configuration: "Configuration",
};
const jobLabels = {
  queued: "En attente",
  running: "En cours",
  succeeded: "Terminé",
  failed: "Échec",
  cancelled: "Arrêté",
};
const jobKinds = { document: "PDF", batch: "Dossier", runtime: "Modèles" };

export function InferenceTable({ calls }: { calls: Record<string, unknown>[] }) {
  if (!calls.length) return null;
  const mib = (value: unknown) => typeof value === 'number' ? `${(value / 1048576).toFixed(0)} Mio` : '—';
  return <div className="inference-table"><table><thead><tr><th>Modèle / device</th><th>Images (px)</th><th>Durée</th><th>Tokens sortie</th><th>Pic VRAM PyTorch observé</th><th>Pic VRAM globale observé</th></tr></thead><tbody>
    {calls.map((call, index) => <tr key={index} className={call.status === 'failed' ? 'failed' : ''}><td>{String(call.model)}<br /><small>{String(call.device)} · {String(call.status)}</small></td><td>{JSON.stringify(call.image_sizes)}</td><td>{Number(call.seconds).toFixed(2)} s</td><td>{call.output_tokens == null ? '—' : String(call.output_tokens)}</td><td>{mib(call.torch_peak_allocated_bytes)}</td><td>{mib(call.cuda_peak_used_bytes)}</td></tr>)}
  </tbody></table><p className="quiet-note">Durées synchronisées en fin d’appel CUDA. VRAM échantillonnée toutes les 50 ms, sans modifier les compteurs du runtime : un pic très bref peut échapper à l’échantillonnage. PyTorch inclut les poids résidents du processus ; la VRAM globale inclut les autres processus et Paddle. La mémoire partagée Windows n’est pas mesurée. Les tokens excluent le padding.</p></div>;
}

export function JobsModal({
  jobs,
  error,
  stoppingBatch,
  onStop,
  onClose,
}: {
  jobs: Job[];
  error: string;
  stoppingBatch: string | null;
  onStop: (id: string) => void;
  onClose: () => void;
}) {
  const [onlyActive, setOnlyActive] = useState(false);
  const [expanded, setExpanded] = useState(new Set<string>());
  const visible = jobs.filter(
    (job) => !onlyActive || job.status === "queued" || job.status === "running",
  );
  return (
    <Modal
      title="Jobs"
      subtitle="Analyses PDF et changements de modèles · 100 derniers jobs de cette session serveur"
      onClose={onClose}
      wide
    >
      <div className="jobs-toolbar">
        <label>
          <input
            type="checkbox"
            checked={onlyActive}
            onChange={(event) => setOnlyActive(event.target.checked)}
          />{" "}
          En cours et en attente
        </label>
        <span>Actualisation automatique</span>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <div
        className="jobs-list"
        aria-label="Historique des jobs"
        aria-live="polite"
      >
        {!visible.length && (
          <p className="quiet-note">
            Aucun job {onlyActive ? "en cours" : "lancé pour le moment"}.
          </p>
        )}
        {visible.map((job) => (
          <article className={`job-card ${job.status}`} key={job.id}>
            <div className="job-heading">
              <strong>{job.name}</strong>
              <span className="job-state">
                {job.stop_requested && (job.status === "queued" || job.status === "running")
                  ? "Arrêt demandé" : jobLabels[job.status]}
              </span>
            </div>
            <p className="job-step">
              {job.status === "running" && (
                <LoaderCircle className="spin" size={14} />
              )}
              {job.stage ? stageLabels[job.stage] : "En attente du worker"}
              {job.detail && <> · {job.detail}</>}
            </p>
            {job.kind === "batch" && (job.status === "queued" || job.status === "running") && (
              <button type="button" className="secondary-button"
                disabled={job.stop_requested || stoppingBatch === job.id} onClick={() => onStop(job.id)}>
                {job.stop_requested || stoppingBatch === job.id ? "Arrêt demandé" : "Arrêter l’extraction"}
              </button>
            )}
            <div className="job-meta">
              <span>{jobKinds[job.kind]}</span>
              <time dateTime={job.created_at}>
                {new Date(job.created_at).toLocaleString()}
              </time>
              <span>
                {job.elapsed_seconds.toFixed(1)} s
                {job.started_at ? "" : " · pas encore démarré"}
              </span>
            </div>
            {job.error && <p className="error-banner">{job.error}</p>}
            <div className="job-meta">{Object.entries(job.stage_seconds).map(([stage, seconds]) => <span key={stage}>{stageLabels[stage as keyof typeof stageLabels] || stage} : {seconds.toFixed(1)} s</span>)}</div>
            {!!job.inference_count && <details><summary>Inférences des modèles injectés ({job.inference_count} appels, 500 derniers affichés)</summary><InferenceTable calls={job.inferences} /></details>}
            {job.events.length > 0 && (
              <details
                onToggle={(event) => {
                  const open = event.currentTarget.open;
                  setExpanded((previous) => {
                    const next = new Set(previous);
                    if (open) next.add(job.id);
                    else next.delete(job.id);
                    return next;
                  });
                }}
              >
                <summary>Étapes traversées ({job.events.length})</summary>
                {expanded.has(job.id) && (
                  <ol className="job-events">
                    {job.events.map((event, index) => (
                      <li key={index}>
                        <time dateTime={event.at}>
                          {new Date(event.at).toLocaleTimeString()}
                        </time>
                        <span>
                          {stageLabels[event.stage]}
                          {event.detail && <> · {event.detail}</>}
                        </span>
                      </li>
                    ))}
                  </ol>
                )}
              </details>
            )}
          </article>
        ))}
      </div>
    </Modal>
  );
}
export function ObjectTree({ value }: { value: unknown }) {
  if (value === null) return <span className="json-null">Aucun</span>;
  if (typeof value !== "object")
    return <span className="json-value">{String(value)}</span>;
  return (
    <div className="object-tree">
      {Object.entries(value as Record<string, unknown>).map(([key, child]) => (
        <div className="object-row" key={key}>
          {child !== null && typeof child === "object" ? (
            <details open>
              <summary>{key}</summary>
              <ObjectTree value={child} />
            </details>
          ) : (
            <>
              <span className="object-key">{key}</span>
              <ObjectTree value={child} />
            </>
          )}
        </div>
      ))}
    </div>
  );
}
export function RootFolderPicker({ onChoose }: { onChoose: (root: string) => void }) {
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title="Choisir le dossier racine des PDF"
      subtitle="Indiquez le chemin du dossier sur la machine qui exécute PdfParse. Ses sous-dossiers seront inclus."
    >
      <form onSubmit={async (event) => {
        event.preventDefault();
        if (busy) return;
        setBusy(true);
        setError("");
        try {
          const { data } = await selectSourceRoot({ body: { path: path.trim() } });
          onChoose(data!.root!);
        } catch (err) {
          setError(errorText(err));
          setBusy(false);
        }
      }}>
        <div className="root-folder-field">
          <label htmlFor="pdf-root-path">Chemin du dossier racine</label>
          <input id="pdf-root-path" autoFocus required value={path} disabled={busy}
            placeholder="/chemin/vers/mes/pdf" onChange={(event) => setPath(event.target.value)} />
          {error && <p className="error-banner" role="alert">{error}</p>}
        </div>
        <footer className="modal-footer">
          <span>Ce choix s’applique à cette session du serveur.</span>
          <button className="primary-button" type="submit" disabled={busy || !path.trim()}>
            {busy ? "Validation…" : "Utiliser ce dossier racine"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}

export function FolderPicker({
  current,
  onChoose,
  onClose,
}: {
  current: string;
  onChoose: (path: string) => void;
  onClose: () => void;
}) {
  const [path, setPath] = useState(current);
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError("");
    listFolders({ query: { path }, signal: controller.signal })
      .then(({ data }) => setListing(data!))
      .catch((err) => {
        if (!controller.signal.aborted) setError(errorText(err));
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [path]);
  return (
    <Modal
      title="Choisir un dossier source"
      subtitle="Parcourez les dossiers de la machine qui exécute PdfParse."
      onClose={onClose}
    >
      <div className="folder-location">
        <FolderOpen size={18} />
        <span>
          {listing
            ? `${listing.root}/${listing.path === "." ? "" : listing.path}`
            : "Chargement…"}
        </span>
      </div>
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      <div className="folder-list" aria-busy={busy}>
        {listing?.parent !== null && listing?.parent !== undefined && (
          <button
            className="folder-row"
            onClick={() => setPath(listing.parent!)}
          >
            <ArrowLeft size={17} /> Dossier parent
          </button>
        )}
        {listing?.folders.map((folder) => (
          <button
            className="folder-row"
            key={folder.path}
            onClick={() => setPath(folder.path)}
          >
            <Folder size={18} />
            <span>{folder.name}</span>
            <ChevronRight size={16} />
          </button>
        ))}
        {!busy && !listing?.folders.length && !error && (
          <p className="quiet-note">
            Aucun sous-dossier. Vous pouvez sélectionner ce dossier.
          </p>
        )}
      </div>
      <footer className="modal-footer">
        <span>Les sous-dossiers seront inclus.</span>
        <button
          className="primary-button"
          disabled={!listing || busy || !!error}
          onClick={() => onChoose(listing!.path)}
        >
          <Check size={16} /> Utiliser ce dossier
        </button>
      </footer>
    </Modal>
  );
}
const modelCategories: { id: ModelCategory; label: string }[] = [
  { id: "layout", label: "Layout" },
  { id: "picture_classifier", label: "Classifieur de figures (DFC)" },
  { id: "ocr", label: "OCR des figures" },
  { id: "vlm", label: "VLM des figures" },
];
const runtimeLabels = {
  unloaded: "Modèles déchargés · extraction native",
  loading: "Chargement des modèles…",
  loaded: "Configuration chargée",
  unloading: "Déchargement des modèles…",
  error: "Échec du chargement",
};
type DraftModel = { name: string; device: string; settings: string };
const emptyDraft = (): Record<ModelCategory, DraftModel> =>
  Object.fromEntries(
    modelCategories.map(({ id }) => [
      id,
      { name: "", device: id === "ocr" || id === "vlm" ? "cuda:0" : "cpu", settings: "{}" },
    ]),
  ) as Record<ModelCategory, DraftModel>;

export function SettingsModal({
  onClose,
  runtimeJob,
  runtime,
  onRuntimeChange,
}: {
  onClose: () => void;
  runtimeJob?: Job;
  runtime: RuntimeStatus | null;
  onRuntimeChange: (runtime: RuntimeStatus) => void;
}) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [options, setOptions] = useState<RuntimeOptions | null>(null);
  const [draft, setDraft] = useState(emptyDraft);
  const [chunking, setChunking] = useState<
    NonNullable<RuntimeConfigurationInput["chunking"]>
  >({ kind: "recursive" });
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [view, setView] = useState<"models" | "objects">("models");
  const busy =
    submitting || !options ||
    runtime?.status === "loading" ||
    runtime?.status === "unloading";
  const [enrichment, setEnrichment] = useState<NonNullable<RuntimeConfigurationInput['enrichment']>>({});
  const [classRoutes, setClassRoutes] = useState('{}');
  let routeModes: string[] = [];
  try { routeModes = Object.values(JSON.parse(classRoutes)); } catch { /* checked on submit */ }
  const modes = draft.picture_classifier.name ? [enrichment.figures, enrichment.fallback, ...routeModes] : [];
  const needsOcr = !!enrichment.text_ocr || modes.some(m => m === 'ocr' || m === 'ocr_vlm' || (m === 'table' && (!!draft.ocr.name || !draft.vlm.name)));
  const needsVlm = !!enrichment.page_vlm || modes.some(m => m === 'vlm' || m === 'ocr_vlm' || (m === 'table' && !!draft.vlm.name));
  const incomplete = (needsOcr && !draft.ocr.name) || (needsVlm && !draft.vlm.name);

  useEffect(() => {
    const controller = new AbortController();
    getRuntimeOptions({ signal: controller.signal })
      .then(({ data }) => {
        setOptions(data!);
        onRuntimeChange(data!.runtime);
        setChunking(data!.defaults.chunking);
        setEnrichment(data!.defaults.enrichment);
        setClassRoutes(JSON.stringify(data!.defaults.enrichment.class_routes, null, 2));
        const config = data!.runtime.requested ?? data!.runtime.configuration;
        if (config) {
          const next = emptyDraft();
          for (const { id } of modelCategories) {
            const model = config[id];
            if (model)
              next[id] = {
                name: model.name,
                device: model.device || "cpu",
                settings: JSON.stringify(model.settings || {}, null, 2),
              };
          }
          setDraft(next);
          setChunking(config.chunking);
          setEnrichment(config.enrichment);
          setClassRoutes(JSON.stringify(config.enrichment.class_routes, null, 2));
        }
      })
      .catch((err) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    setSettings(null);
    if (!runtime || busy || runtime.status === "error") return;
    const controller = new AbortController();
    getSettings({ signal: controller.signal })
      .then(({ data }) => setSettings(data!))
      .catch((err) => {
        if (!controller.signal.aborted) setError(errorText(err));
      });
    return () => controller.abort();
  }, [runtime?.status, runtime?.revision, busy]);

  function choose(id: ModelCategory, name: string) {
    const model = options?.models.find(
      (item) => item.category === id && item.name === name,
    );
    setDraft((previous) => ({
      ...previous,
      [id]: {
        ...previous[id],
        name,
        device: !previous[id].name && (id === "ocr" || id === "vlm")
          ? options?.devices.find((device) => device.id.startsWith("cuda:"))?.id ?? "cuda:0"
          : previous[id].device,
        settings: JSON.stringify(model?.defaults || {}, null, 2),
      },
    }));
  }
  function localProfile() {
    if (!options) return;
    const profile = options.defaults;
    const next = emptyDraft();
    for (const { id } of modelCategories) {
      const choice = profile[id];
      if (!choice) continue;
      const model = options.models.find(item =>
        item.category === id && item.name === choice.name && item.prepared);
      if (!model) {
        setError(`Poids à préparer : ${id}/${choice.name}`);
        return;
      }
      next[id] = {
        name: choice.name,
        device: choice.device,
        settings: JSON.stringify(choice.settings, null, 2),
      };
    }
    setDraft(next);
    setChunking(profile.chunking);
    setEnrichment(profile.enrichment);
    setClassRoutes(JSON.stringify(profile.enrichment.class_routes, null, 2));
    setError("");
  }
  async function apply(unload = false) {
    setError("");
    setSubmitting(true);
    try {
      if (unload) {
        const { data } = await unloadRuntime();
        onRuntimeChange(data!);
      } else {
        const body: RuntimeConfigurationInput = { chunking, enrichment: { ...enrichment, class_routes: JSON.parse(classRoutes) } };
        for (const { id } of modelCategories) {
          const model = draft[id];
          const values = model.name ? JSON.parse(model.settings) : {};
          if (!values || typeof values !== "object" || Array.isArray(values))
            throw new Error(
              `${id} : les paramètres doivent être un objet JSON.`,
            );
          body[id] = model.name
            ? ({
              name: model.name,
              device: model.device,
              settings: values,
            } as ModelSelectionInput)
            : null;
        }
        const { data } = await loadRuntime({ body });
        onRuntimeChange(data!);
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSubmitting(false);
    }
  }
  return (
    <Modal
      title="Paramètres de la pipeline"
      subtitle="Choisissez les services injectés, leurs paramètres et leur device."
      onClose={onClose}
      wide
    >
      <div className="runtime-tabs" role="tablist" aria-label="Paramètres">
        <button
          role="tab"
          aria-selected={view === "models"}
          onClick={() => setView("models")}
        >
          Modèles et découpage
        </button>
        <button
          role="tab"
          aria-selected={view === "objects"}
          onClick={() => setView("objects")}
        >
          Objets utilisés · lecture seule
        </button>
      </div>
      <div className="settings-body">
        {error && (
          <p className="error-banner" role="alert">
            {error}
          </p>
        )}
        {runtime?.error && (
          <p className="error-banner" role="alert">
            {runtime.error}
          </p>
        )}
        <p className="runtime-status" role="status">
          {busy && <LoaderCircle className="spin" size={16} />}
          {runtime
            ? runtimeLabels[runtime.status]
            : "Lecture de la configuration…"}
        </p>
        {busy && runtimeJob?.stage && (
          <p className="quiet-note" role="status">
            {stageLabels[runtimeJob.stage]}
            {runtimeJob.detail && ` · ${runtimeJob.detail}`}
          </p>
        )}
        {view === "models" && options && (
          <>
            <p className="quiet-note">
              Choisissez vos modèles ou préremplissez le profil par défaut, puis
              cliquez sur « Charger et appliquer ». Le téléchargement des poids
              seul n’active pas les modèles. Après application, les résultats déjà
              en cache pour cette configuration sont disponibles automatiquement.
            </p>
            {options.devices.some((device) =>
              device.id.startsWith("cuda:"),
            ) && (
                <button
                  className="secondary-button"
                  disabled={busy}
                  onClick={localProfile}
                >
                  Préremplir le profil par défaut · GPU 4 bits
                </button>
              )}
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void apply();
              }}
            >
              <fieldset className="runtime-fields" disabled={busy}>
                <section className="routing-settings">
                  <h3>Quoi analyser ?</h3>
                  <p className="quiet-note">Le VLM des pages et l’OCR des blocs sont indépendants des figures. Un modèle sélectionné n’est chargé que si une étape active l’utilise. Décochez les pages sans modifier le VLM des figures.</p>
                  <label><input type="checkbox" checked={!!enrichment.text_ocr} onChange={e => setEnrichment({ ...enrichment, text_ocr: e.target.checked })} /> OCR des blocs textuels scannés</label>
                  <label><input type="checkbox" checked={!!enrichment.page_vlm} onChange={e => setEnrichment({ ...enrichment, page_vlm: e.target.checked })} /> Description VLM de chaque page (coûteux)</label>
                  {(['figures', 'fallback'] as const).map(key => <label key={key}>{key === 'figures' ? 'Figures : règle par défaut' : 'Classe incertaine : repli'}
                    <select value={enrichment[key]} onChange={e => setEnrichment({ ...enrichment, [key]: e.target.value as typeof enrichment.figures })}>
                      <option value="keep">Conserver sans OCR/VLM</option><option value="ocr">OCR seul</option><option value="vlm">VLM seul</option><option value="ocr_vlm">OCR puis VLM</option><option value="table">Tableau (OCR/VLM Markdown)</option>
                    </select></label>)}
                  <label>Seuil de confiance DFC <input type="number" min="0" max="1" step="0.05" value={enrichment.confidence_threshold} onChange={e => setEnrichment({ ...enrichment, confidence_threshold: Number(e.target.value) })} /></label>
                  <label>Exceptions par classe DFC (JSON)<textarea aria-label="Routes par classe DFC" value={classRoutes} onChange={e => setClassRoutes(e.target.value)} placeholder={'{"bar_chart":"ocr_vlm","logo":"keep"}'} /></label>
                  <p className="quiet-note">Valeurs : keep, ocr, vlm, ocr_vlm, table. Les classes absentes utilisent la règle par défaut. Aucun appel OCR/VLM pour « conserver ».</p>
                </section>

                {modelCategories.map(({ id, label }) => {
                  const model = options.models.find(
                    (item) =>
                      item.category === id && item.name === draft[id].name,
                  );
                  return (
                    <section className="model-field" key={id}>
                      <label htmlFor={`model-${id}`}>{label}</label>
                      <div className="model-selects">
                        <select
                          id={`model-${id}`}
                          value={draft[id].name}
                          onChange={(event) => choose(id, event.target.value)}
                        >
                          <option value="">
                            {id === "layout"
                              ? "Heron natif Docling"
                              : "Désactivé"}
                          </option>
                          {options.models
                            .filter((item) => item.category === id)
                            .map((item) => (
                              <option
                                key={item.name}
                                value={item.name}
                                disabled={!item.prepared}
                              >
                                {item.name}
                                {item.prepared ? "" : " · poids à télécharger"}
                              </option>
                            ))}
                        </select>
                        <select
                          aria-label={`Device ${label}`}
                          value={draft[id].device}
                          disabled={!draft[id].name}
                          onChange={(event) =>
                            setDraft((previous) => ({
                              ...previous,
                              [id]: {
                                ...previous[id],
                                device: event.target.value,
                              },
                            }))
                          }
                        >
                          {!options.devices.some(
                            (device) => device.id === draft[id].device,
                          ) && (
                              <option value={draft[id].device}>
                                {draft[id].device} · indisponible
                              </option>
                            )}
                          {options.devices.map((device) => (
                            <option key={device.id} value={device.id}>
                              {device.label}
                            </option>
                          ))}
                        </select>
                      </div>
                      {id === "ocr" && draft[id].name && (
                        <p className="quiet-note">
                          {draft.layout.name
                            ? "Ce service lit les blocs textuels scannés et les figures."
                            : "Sélectionnez aussi un modèle de layout pour lire le corps des scans. Sans ce choix, l’OCR traite les figures."}
                        </p>
                      )}
                      {draft[id].name && (
                        <details className="model-options">
                          <summary>Paramètres du modèle (JSON)</summary>
                          <textarea
                            aria-label={`Paramètres ${label}`}
                            spellCheck={false}
                            value={draft[id].settings}
                            onChange={(event) =>
                              setDraft((previous) => ({
                                ...previous,
                                [id]: {
                                  ...previous[id],
                                  settings: event.target.value,
                                },
                              }))
                            }
                          />
                          <details>
                            <summary>Options acceptées</summary>
                            <ObjectTree value={model?.settings_schema} />
                          </details>
                        </details>
                      )}
                    </section>
                  );
                })}
                <section className="model-field">
                  <h3>Découpage des chunks</h3>
                  <label className="chunk-size">
                    Type de découpage
                    <select
                      value={chunking.kind}
                      onChange={(event) => {
                        const chunk_size = chunking.chunk_size ?? options?.defaults.chunking.chunk_size ?? 1;
                        if (event.target.value === "sentence")
                          setChunking({
                            kind: "sentence",
                            chunk_size,
                            chunk_overlap: 0,
                            min_sentences_per_chunk: 1,
                            min_characters_per_sentence: 12,
                          });
                        else if (event.target.value === "structural")
                          setChunking({
                            kind: "structural",
                            chunk_size: Math.min(chunk_size || 512, 512),
                            target_tokens: 384,
                            min_tokens: 128,
                            respect_page_boundaries: false,
                          });
                        else if (event.target.value === "recursive")
                          setChunking({
                            kind: "recursive",
                            chunk_size,
                            min_characters_per_chunk: 24,
                          });
                        else
                          setChunking({
                            kind: "token",
                            chunk_size,
                            chunk_overlap: 0,
                          });
                      }}
                    >
                      <option value="structural">Structure · tableaux, listes, sections</option>
                      <option value="token">Taille fixe</option>
                      <option value="sentence">Par phrases</option>
                      <option value="recursive">
                        Récursif · paragraphes, phrases, mots
                      </option>
                    </select>
                  </label>
                  <label className="chunk-size">
                    {chunking.kind === "structural" ? "Plafond strict (tokens E5)" : "Taille cible (caractères)"}
                    <input
                      type="number"
                      min={1}
                      max={chunking.kind === "structural" ? 512 : 100000}
                      required
                      value={chunking.chunk_size ?? options?.defaults.chunking.chunk_size ?? 1}
                      onChange={(event) =>
                        setChunking({
                          ...chunking,
                          chunk_size: event.target.valueAsNumber,
                        })
                      }
                    />
                  </label>
                  {(chunking.kind === "token" || chunking.kind === "sentence") && (
                    <label className="chunk-size">
                      Chevauchement (caractères)
                      <input
                        type="number"
                        min={0}
                        max={(chunking.chunk_size ?? options?.defaults.chunking.chunk_size ?? 1) - 1}
                        required
                        value={chunking.chunk_overlap ?? 0}
                        onChange={(event) =>
                          setChunking({
                            ...chunking,
                            chunk_overlap: event.target.valueAsNumber,
                          })
                        }
                      />
                    </label>
                  )}
                  {chunking.kind === "sentence" && (
                    <>
                      <label className="chunk-size">
                        Minimum de phrases par chunk
                        <input
                          type="number"
                          min={1}
                          required
                          value={chunking.min_sentences_per_chunk ?? 1}
                          onChange={(event) =>
                            setChunking({
                              ...chunking,
                              min_sentences_per_chunk:
                                event.target.valueAsNumber,
                            })
                          }
                        />
                      </label>
                      <label className="chunk-size">
                        Longueur minimale des phrases (caractères)
                        <input
                          type="number"
                          min={1}
                          required
                          value={chunking.min_characters_per_sentence ?? 12}
                          onChange={(event) =>
                            setChunking({
                              ...chunking,
                              min_characters_per_sentence:
                                event.target.valueAsNumber,
                            })
                          }
                        />
                      </label>
                    </>
                  )}
                  {chunking.kind === "recursive" && (
                    <label className="chunk-size">
                      Seuil de regroupement (caractères)
                      <input
                        type="number"
                        min={1}
                        required
                        value={chunking.min_characters_per_chunk ?? 24}
                        onChange={(event) =>
                          setChunking({
                            ...chunking,
                            min_characters_per_chunk:
                              event.target.valueAsNumber,
                          })
                        }
                      />
                    </label>
                  )}
                  {chunking.kind === "structural" && (
                    <>
                      <label className="chunk-size">
                        Cible de regroupement (tokens E5)
                        <input
                          type="number"
                          min={1}
                          max={chunking.chunk_size ?? 512}
                          value={chunking.target_tokens ?? 384}
                          onChange={(event) =>
                            setChunking({
                              ...chunking,
                              target_tokens: event.target.valueAsNumber,
                            })
                          }
                        />
                      </label>
                      <label className="chunk-size">
                        Minimum souhaité (tokens E5)
                        <input
                          type="number"
                          min={1}
                          max={chunking.target_tokens ?? 384}
                          value={chunking.min_tokens ?? 128}
                          onChange={(event) =>
                            setChunking({
                              ...chunking,
                              min_tokens: event.target.valueAsNumber,
                            })
                          }
                        />
                      </label>
                      <label className="structure-toggle">
                        <input
                          type="checkbox"
                          checked={Boolean(chunking.respect_page_boundaries)}
                          onChange={(event) =>
                            setChunking({
                              ...chunking,
                              respect_page_boundaries: event.target.checked,
                            })
                          }
                        />
                        Respecter les limites de pages
                      </label>
                    </>
                  )}
                  <p className="quiet-note">
                    {chunking.kind === "structural"
                      ? "Découpage par structure, lignes de tableaux et listes. Le budget inclut les titres de section."
                      : chunking.kind === "token"
                        ? "Découpage à taille fixe avec conservation exacte du texte et du chevauchement."
                        : chunking.kind === "sentence"
                          ? "Les limites suivent les phrases. Une phrase longue peut dépasser la taille cible ; le chevauchement retient des phrases entières qui tiennent dans le budget."
                          : "Découpage successif par paragraphes, phrases, mots, puis caractères si nécessaire. Sans chevauchement."}{" "}
                    {chunking.kind !== "structural" && "Les titres de section sont ajoutés au contexte, hors budget. "}
                    Aucun modèle ni téléchargement supplémentaire.
                  </p>
                </section>
                <p className="quiet-note">
                  Enrichissement natif : tableaux orientés recherche,
                  formulaires indexables, filtrage des en-têtes/pieds de page et
                  contexte de section.
                </p>
                {incomplete && (
                  <p className="error-banner">
                    Sélectionnez les services requis par les étapes et routes activées.
                  </p>
                )}
                <div className="runtime-actions">
                  <button
                    type="submit"
                    className="primary-button"
                    disabled={incomplete}
                  >
                    Charger et appliquer
                  </button>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => void apply(true)}
                  >
                    Décharger les modèles
                  </button>
                </div>
              </fieldset>
            </form>
          </>
        )}
        {view === "objects" && settings && (
          <>
            <div className="pipeline-class">
              <Sparkles size={18} />
              <code>{settings.pipeline_class}</code>
            </div>
            <div className="settings-stats">
              <span>
                <b>{settings.worker_count}</b> worker
              </span>
              <span>
                <b>{settings.cache_capacity}</b> documents en cache
              </span>
            </div>
            <h3>Composants injectés</h3>
            <ObjectTree value={settings.configuration} />
            <h3>Extracteur</h3>
            <ObjectTree value={settings.extractor} />
            <h3>Options de l’extracteur</h3>
            <ObjectTree value={settings.extraction_options} />
            <h3>Codecs et versions des wrappers</h3>
            <ObjectTree value={settings.codecs} />
          </>
        )}
      </div>
    </Modal>
  );
}
