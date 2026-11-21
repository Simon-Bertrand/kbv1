import { test, expect } from "@playwright/test";

test('model defaults are supplied by the server rather than copied in the UI', async ({page}) => {
  let compactProfile: unknown;
  let appliedProfile: unknown;
  await page.route('**/api/runtime', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    appliedProfile = route.request().postDataJSON();
    await route.fulfill({json: {status: 'unloaded', configuration: appliedProfile, requested: appliedProfile, revision: 1, error: null}});
  });
  await page.route('**/api/runtime/options', async route => {
    const data = await (await route.fetch()).json();
    data.defaults.chunking.chunk_size = 1536;
    data.defaults.enrichment.confidence_threshold = 0.65;
    data.devices.push({id: 'cuda:0', label: 'GPU de test'});
    data.models.forEach((model: {prepared: boolean; defaults: Record<string, unknown>}) => {
      model.prepared = true;
      model.defaults.implicit_setting = 'must not enter the compact profile';
    });
    compactProfile = data.defaults;
    await route.fulfill({json: data});
  });
  await page.goto('/');
  await page.getByRole('button', {name: 'Paramètres', exact: true}).click();
  const modal = page.getByRole('dialog');
  await modal.getByRole('button', {name: 'Préremplir le profil par défaut · GPU 4 bits'}).click();
  await expect(modal.getByLabel('Taille cible (caractères)')).toHaveValue('1536');
  await expect(modal.getByLabel('Seuil de confiance DFC')).toHaveValue('0.65');
  await expect(modal.getByLabel('Classifieur de figures (DFC)', {exact: true})).toHaveValue('dfc_v25');
  await expect(modal.getByLabel('VLM des figures', {exact: true})).toHaveValue('qwen3_5_4b');
  await modal.getByRole('button', {name: 'Charger et appliquer', exact: true}).click();
  await expect.poll(() => appliedProfile).toEqual(compactProfile);
});

test('PDF pages scroll continuously and render only nearby canvases', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', {name: 'Choisir un dossier'}).click();
  await page.getByRole('button', {name: 'Utiliser ce dossier'}).click();
  await page.getByRole('button', {name: /ZLong.pdf/}).click();
  await expect(page.locator('[data-pdf-page="1"] canvas')).toHaveAttribute('data-page', '1');
  const viewer = page.getByLabel('Visionneuse PDF');
  const bounds = await viewer.boundingBox();
  await page.mouse.move(bounds!.x + bounds!.width / 2, bounds!.y + bounds!.height / 2);
  await page.mouse.wheel(0, 700);
  await expect(page.getByLabel('Numéro de page')).toHaveValue('2');
  await expect(page.locator('[data-pdf-page="2"] canvas')).toHaveAttribute('data-page', '2');
  await page.getByLabel('Numéro de page').fill('15');
  await expect(page.locator('[data-pdf-page="15"] canvas')).toHaveAttribute('data-page', '15');
  await expect(page.locator('[data-pdf-page="15"]')).toBeInViewport();
  await expect.poll(() => page.locator('canvas').count()).toBeLessThan(10);
  await page.getByRole('button', {name: 'Agrandir', exact: true}).click();
  await expect(page.getByLabel('Numéro de page')).toHaveValue('15');
  await expect(page.locator('[data-pdf-page="15"]')).toBeInViewport();
  await page.getByLabel('Numéro de page').fill('1');
  await expect(page.locator('[data-pdf-page="1"] canvas')).toHaveAttribute('data-page', '1');
  await expect(page.locator('[data-pdf-page="1"]')).toBeInViewport();
});

test('PDF pagination and search cover the full recursive corpus', async ({ page }) => {
  const requests: URL[] = [];
  await page.route('**/api/documents?*', async route => {
    const url = new URL(route.request().url());
    requests.push(url);
    const response = await route.fetch();
    const actual = await response.json();
    const current = Number(url.searchParams.get('page') || '1');
    const search = url.searchParams.get('search') || '';
    await route.fulfill({json: {...actual, total: search ? 1 : 101, page: search ? 1 : current,
      documents: search ? actual.documents.slice(0, 1) : actual.documents }});
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Choisir un dossier' }).click();
  await page.getByRole('button', { name: 'Utiliser ce dossier' }).click();
  await expect(page.getByLabel('Pagination des PDF')).toContainText('Page 1 / 3');
  await page.getByRole('button', { name: 'PDF suivants', exact: true }).click();
  await expect(page.getByLabel('Pagination des PDF')).toContainText('Page 2 / 3');
  expect(requests.at(-1)?.searchParams.get('page')).toBe('2');
  await page.getByLabel('Rechercher un PDF').fill('Guide');
  await expect(page.getByLabel('Pagination des PDF')).toContainText('Page 1 / 1');
  expect(requests.at(-1)?.searchParams.get('search')).toBe('Guide');
  await expect(page.getByRole('button', { name: 'PDF suivants', exact: true })).toBeDisabled();
});

test('running the current profile on the folder includes PDFs hidden by search', async ({ page }) => {
  await page.goto('/');
  const analyzeAll = page.getByRole('button', { name: "Lancer l'extraction", exact: true });
  await expect(analyzeAll).toBeDisabled();
  await page.getByRole('button', { name: 'Choisir un dossier' }).click();
  await page.getByRole('button', { name: 'Rapports', exact: true }).click();
  await page.getByRole('button', { name: 'Utiliser ce dossier' }).click();
  await expect(page.getByRole('button', { name: /Annexe.pdf/ })).toBeVisible();

  await page.getByRole('textbox', { name: 'Rechercher un PDF' }).fill('absent');
  await expect(page.getByText('Aucun PDF ne correspond.')).toBeVisible();
  await analyzeAll.click();

  const jobs = page.getByRole('dialog');
  await expect(jobs.getByRole('heading', { name: 'Jobs' })).toBeVisible();
  const batch = jobs.locator('.job-card').filter({ hasText: 'Analyse du dossier · Rapports' });
  await expect(batch).toContainText('Terminé');
  await expect(batch).toContainText('1/1 PDF analysés');
  await expect(jobs.locator('.job-card').filter({ hasText: 'Rapports/Annexe.pdf' })).toContainText('Terminé');
});

test('PDF viewer survives rapid zoom and document changes', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByRole('button', { name: 'Choisir un dossier' }).click();
  await page.getByRole('button', { name: 'Utiliser ce dossier' }).click();
  await page.getByRole('button', { name: /Guide.pdf/ }).click();
  await expect(page.locator('.pdf-page[data-current-page="true"] canvas')).toHaveAttribute('data-page', '1');
  for (let index = 0; index < 5; index++) await page.getByRole('button', {name: 'Agrandir', exact: true}).click();
  await page.getByRole('button', { name: /Annexe.pdf/ }).click();
  await page.getByRole('button', { name: /Guide.pdf/ }).click();
  await expect(page.locator('.pdf-page[data-current-page="true"] canvas')).toHaveAttribute('data-page', '1');
  await page.getByLabel('Numéro de page').fill('2');
  await expect(page.locator('.pdf-page[data-current-page="true"] canvas')).toHaveAttribute('data-page', '2');
  expect(errors).toEqual([]);
});

test('PDF viewer handles rotated pages and password-protected PDFs', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Choisir un dossier' }).click();
  await page.getByRole('button', { name: 'Utiliser ce dossier' }).click();
  await page.getByRole('button', { name: /ZRotation.pdf/ }).click();
  await expect(page.locator('.pdf-page[data-current-page="true"] canvas')).toHaveAttribute('data-page', '1');
  const bounds = await page.locator('.pdf-page[data-current-page="true"] canvas').boundingBox();
  expect(bounds!.width).toBeGreaterThan(bounds!.height);
  await page.getByRole('button', { name: /ZProtected.pdf/ }).click();
  await page.getByLabel('Mot de passe du PDF').fill('wrong');
  await page.getByRole('button', { name: 'Ouvrir le PDF', exact: true }).click();
  await expect(page.getByLabel('Mot de passe du PDF')).toBeVisible();
  await page.getByLabel('Mot de passe du PDF').fill('fixture-password');
  await page.getByRole('button', { name: 'Ouvrir le PDF', exact: true }).click();
  await expect(page.locator('.pdf-page[data-current-page="true"] canvas')).toHaveAttribute('data-page', '1');
});

test("missing source root opens a required path dialog before any PDF browsing", async ({ page }) => {
  let browsingCalls = 0;
  const selected = "/tmp/pdfparse-studio-fixture";
  page.on("request", (request) => {
    if (/\/api\/(folders|documents)(\?|$)/.test(request.url())) browsingCalls++;
  });
  await page.route("**/api/source", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { root: null } });
    } else if (route.request().postDataJSON().path !== selected) {
      await route.fulfill({ status: 404, json: { detail: "Chemin introuvable." } });
    } else {
      await route.fulfill({ json: { root: selected } });
    }
  });
  await page.goto("/");
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Choisir le dossier racine des PDF" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Utiliser ce dossier racine" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  expect(browsingCalls).toBe(0);
  await dialog.getByLabel("Chemin du dossier racine").fill("/missing");
  await dialog.getByRole("button", { name: "Utiliser ce dossier racine" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Chemin introuvable");
  expect(browsingCalls).toBe(0);
  await dialog.getByLabel("Chemin du dossier racine").fill(selected);
  await dialog.getByRole("button", { name: "Utiliser ce dossier racine" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Guide.pdf/ })).toBeVisible();
});

test("idle polling slows down, pauses in the background and refreshes on return", async ({ page }) => {
  await page.clock.install({ time: new Date("2030-01-01T00:00:00Z") });
  await page.clock.pauseAt(new Date("2030-01-01T00:00:01Z"));
  const calls = { jobs: 0, runtime: 0 };
  await page.route("**/api/jobs", async (route) => {
    calls.jobs++;
    await route.fulfill({ json: [] });
  });
  await page.route("**/api/runtime", async (route) => {
    calls.runtime++;
    await route.fulfill({ json: {
      status: "unloaded", configuration: null, requested: null, error: null, revision: 0,
    } });
  });
  await page.goto("/");
  await expect.poll(() => calls).toEqual({ jobs: 1, runtime: 1 });
  await page.clock.runFor(59000);
  expect(calls).toEqual({ jobs: 1, runtime: 1 });
  await page.clock.runFor(2000);
  await expect.poll(() => calls).toEqual({ jobs: 2, runtime: 2 });

  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.runFor(60000);
  expect(calls).toEqual({ jobs: 2, runtime: 2 });
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => calls).toEqual({ jobs: 3, runtime: 3 });
});

test("document jobs poll every five seconds without speeding up runtime requests", async ({ page }) => {
  await page.clock.install({ time: new Date("2030-01-01T00:00:00Z") });
  await page.clock.pauseAt(new Date("2030-01-01T00:00:01Z"));
  const calls = { jobs: 0, runtime: 0 };
  let running = true;
  await page.route("**/api/jobs", async (route) => {
    calls.jobs++;
    await route.fulfill({ json: running ? [{
      id: "busy", kind: "document", name: "PDF", document_id: "other",
      status: "running", stage: "ocr", detail: null,
      created_at: "2030-01-01T00:00:00Z", started_at: "2030-01-01T00:00:00Z",
      finished_at: null, elapsed_seconds: 1, error: null, events: [],
    }] : [] });
  });
  await page.route("**/api/runtime", async (route) => {
    calls.runtime++;
    await route.fulfill({ json: {
      status: "loaded", configuration: null, requested: null, error: null, revision: 0,
    } });
  });
  await page.goto("/");
  await expect.poll(() => calls.jobs).toBe(2); // Initial read, then switch to active cadence.
  await expect.poll(() => calls.runtime).toBe(1);
  await page.clock.runFor(4000);
  expect(calls).toEqual({ jobs: 2, runtime: 1 });
  await page.clock.runFor(1100);
  await expect.poll(() => calls).toEqual({ jobs: 3, runtime: 1 });
  running = false;
  await page.clock.runFor(5000);
  await expect.poll(() => calls.jobs).toBe(5); // Completion, then immediate idle refresh.
  await page.clock.runFor(45000);
  expect(calls).toEqual({ jobs: 5, runtime: 1 });
});

test("a slow status request never overlaps the next poll", async ({ page }) => {
  await page.clock.install({ time: new Date("2030-01-01T00:00:00Z") });
  await page.clock.pauseAt(new Date("2030-01-01T00:00:01Z"));
  let calls = 0;
  let release!: () => void;
  const responseReady = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/runtime", async (route) => {
    calls++;
    await responseReady;
    await route.fulfill({ json: {
      status: "unloaded", configuration: null, requested: null, error: null, revision: 0,
    } });
  });
  await page.goto("/");
  await expect.poll(() => calls).toBe(1);
  await page.clock.runFor(45000);
  expect(calls).toBe(1);
  const response = page.waitForResponse("**/api/runtime");
  release();
  await response;
  await page.clock.runFor(100);
  await page.clock.runFor(61000);
  await expect.poll(() => calls).toBe(2);
});

test("browse, PDF rendering, processing, provenance, Markdown and multipage chunks", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Explorez vos documents" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Choisir un dossier" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Utiliser ce dossier" }).click();
  await page.getByRole("button", { name: /Guide.pdf/ }).click();
  await expect(page.locator('.pdf-page[data-current-page="true"] canvas')).toBeVisible();
  await expect(page.getByLabel("Numéro de page")).toHaveValue("1");
  await page.getByRole("button", { name: "Analyser le document" }).click();
  await expect(
    page.getByRole("button", { name: "Analyse terminée" }),
  ).toBeVisible({ timeout: 15_000 });
  await page.getByText("Qualité et préparation RAG", { exact: true }).click();
  await expect(page.getByText("Couverture du texte extrait : 100.0 %")).toBeVisible();
  await page.getByRole("button", { name: "Mémoriser comme référence de comparaison" }).click();
  await expect(page.getByText("Comparaison avec la référence mémorisée")).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Exporter JSONL", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.jsonl$/);
  expect(await download.failure()).toBeNull();
  await page.getByRole("button", { name: /^Jobs/ }).click();
  const job = page
    .locator(".job-card")
    .filter({ hasText: "Guide.pdf" })
    .first();
  await expect(job).toContainText("Terminé");
  await job.getByText(/Étapes traversées/).click();
  await expect(job.locator(".job-events")).toContainText(
    "Extraction et layout",
  );
  await expect(job.locator(".job-events")).toContainText(
    "Découpage des chunks",
  );
  await expect(job.locator(".job-events")).toContainText(
    "Markdown et résultats",
  );
  await page.screenshot({
    path: "/tmp/pdfparse-studio-jobs.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Fermer", exact: true }).click();
  const body = page.locator('[data-element-ref="#/body"]');
  await expect(body).toHaveCount(0);
  await page.getByRole("button", { name: /Information de la page 1/ }).click();
  await expect(page.getByText("PROVENANCE", { exact: true })).toBeVisible();
  await expect(page.locator('.pdf-page[data-current-page="true"]').getByTestId("pdf-highlight")).toHaveCount(1);
  const bounds = await page.locator('.pdf-page[data-current-page="true"]').getByTestId("pdf-highlight").boundingBox();
  expect(bounds?.height).toBeGreaterThan(5);
  await page.getByRole("button", { name: /Voir le parent/ }).click();
  await expect(page.getByLabel("Afficher la structure")).toBeChecked();
  await expect(body).toHaveClass(/selected/);
  await expect(page.getByText("Texte original", { exact: true })).toHaveCount(
    0,
  );
  await page
    .getByRole("button", { name: "Voir #/texts/2", exact: true })
    .click();
  await expect(page.locator(".item-card.selected")).toContainText(
    "Information de la page 1",
  );
  await page.getByLabel("Afficher la structure").uncheck();
  await expect(body).toHaveCount(0);
  await page.getByRole("tab", { name: /Markdown/ }).click();
  await expect(page.locator(".markdown-content h1")).toHaveText("Guide PDF");
  await page.getByRole("button", { name: "Source", exact: true }).click();
  await expect(page.locator(".markdown-content pre")).toContainText(
    "# Guide PDF",
  );
  await page.getByRole("tab", { name: /Chunks/ }).click();
  await page.locator(".chunk-summary").first().click();
  await expect(page.locator('.pdf-page[data-current-page="true"]').getByTestId("pdf-highlight")).toHaveCount(1);
  await expect(page.locator(".page-links")).toContainText("2");
  await page
    .locator(".page-links")
    .getByRole("button", { name: "2", exact: true })
    .click();
  await expect(page.getByLabel("Numéro de page")).toHaveValue("2");
  await expect(page.locator('.pdf-page[data-current-page="true"] canvas')).toHaveAttribute("data-page", "2");
  await expect(page.locator('.pdf-page[data-current-page="true"]').getByTestId("pdf-highlight")).toHaveCount(1);
  await page.screenshot({
    path: "/tmp/pdfparse-studio-chunks.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("jobs show live stages, errors and active filtering", async ({ page }) => {
  const now = new Date().toISOString();
  const job = {
    id: "live-job",
    kind: "document",
    name: "Lettre scannée.pdf",
    document_id: "letter",
    status: "running",
    stage: "ocr",
    detail: "Reconnaissance de la figure",
    created_at: now,
    started_at: now,
    finished_at: null as string | null,
    elapsed_seconds: 1.2,
    stage_seconds: {ocr: 1.2},
    inferences: [],
    inference_count: 0,
    error: null as string | null,
    events: [{ stage: "ocr", at: now, detail: "Reconnaissance de la figure" }],
  };
  await page.route("**/api/jobs", (route) => route.fulfill({ json: [job] }));
  await page.goto("/");
  await page.getByRole("button", { name: /^Jobs/ }).click();
  await expect(page.locator(".job-step")).toContainText("OCR");
  await expect(page.locator(".job-meta").first()).toContainText("1.2 s");
  job.stage = "vlm";
  job.detail = "Description visuelle";
  job.elapsed_seconds = 2.4;
  job.events.push({ stage: "vlm", at: now, detail: "Description visuelle" });
  await expect(page.locator(".job-step")).toContainText("Description VLM", { timeout: 10000 });
  await page.getByLabel("En cours et en attente").check();
  job.status = "failed";
  job.error = "Mémoire GPU insuffisante";
  job.finished_at = now;
  await expect(page.getByText("Aucun job en cours.")).toBeVisible({ timeout: 10000 });
  await page.getByLabel("En cours et en attente").uncheck();
  await expect(page.locator(".job-card")).toContainText("Échec");
  await expect(page.locator(".job-card")).toContainText(
    "Mémoire GPU insuffisante",
  );
  await page.getByText(/Étapes traversées/).click();
  await expect(page.locator(".job-events li")).toHaveCount(2);
});

test("settings expose model selection and a read-only view of actual objects", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Paramètres" }).click();
  const modal = page.getByRole("dialog");
  await expect(
    modal.getByRole("heading", { name: "Paramètres de la pipeline" }),
  ).toBeVisible();
  await expect(
    modal.getByLabel("OCR des figures", { exact: true }),
  ).toBeVisible();
  await modal.getByRole("tab", { name: /Objets utilisés/ }).click();
  await expect(
    modal.getByText("pdfparse.pipeline.PdfParsePipeline", { exact: true }),
  ).toBeVisible();
  await expect(modal.getByRole("tab", { name: /lecture seule/ })).toBeVisible();
  await expect(modal.locator("input, select, textarea")).toHaveCount(0);
  await page.screenshot({
    path: "/tmp/pdfparse-studio-settings.png",
    fullPage: true,
  });
  await page.keyboard.press("Escape");
  await expect(modal).toHaveCount(0);
});

test("element boxes appear by default and select their cards, even with an active filter", async ({
  page,
}) => {
  await page.route("**/api/documents/*/result", async (route) => {
    const response = await route.fetch();
    const result = await response.json();
    // A second box on the first page and enough rows to require scrolling.
    result.items[0].locations = [
      {
        page_no: 1,
        bbox: [10, 10, 100, 30],
        coord_origin: "TOPLEFT",
        charspan: null,
      },
    ];
    result.items.splice(
      2,
      0,
      ...Array.from({ length: 30 }, (_, index) => ({
        ...result.items[1],
        ref: `#/fixture/${index}`,
        locations: [],
        content_for_llm: `Structure ${index}`,
        original_text: `Structure ${index}`,
      })),
    );
    await route.fulfill({ response, json: result });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Choisir un dossier" }).click();
  await page.getByRole("button", { name: "Utiliser ce dossier" }).click();
  await page.getByRole("button", { name: /Guide.pdf/ }).click();
  const analyse = page.getByRole("button", { name: "Analyser le document" });
  if (await analyse.isVisible()) await analyse.click();
  await expect(
    page.getByRole("button", { name: "Analyse terminée" }),
  ).toBeVisible();
  await expect(page.locator('.pdf-page[data-current-page="true"]').getByTestId("pdf-highlight")).toHaveCount(2);
  await expect(page.locator(".item-card.selected")).toHaveCount(0);
  await page.getByLabel("Filtrer les éléments").fill("Guide");
  await expect(page.locator(".item-card")).toHaveCount(1);
  const box = page.locator('[data-source-ref="#/texts/2"]');
  await box.click();
  await expect(box).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Filtrer les éléments")).toHaveValue("");
  const selected = page.locator(".item-card.selected");
  await expect(selected).toHaveAttribute("data-element-ref", "#/texts/2");
  await expect(selected).toBeInViewport();
  await expect(selected).toContainText("Information de la page 1");
  await page.getByRole("button", { name: "Page suivante" }).click();
  await expect(page.locator('.pdf-page[data-current-page="true"] canvas')).toHaveAttribute("data-page", "2");
  await expect(page.locator('.pdf-page[data-current-page="true"]').getByTestId("pdf-highlight")).toHaveCount(1);
  await page.locator('.pdf-page[data-current-page="true"]').getByTestId("pdf-highlight").click();
  await expect(selected).toHaveAttribute("data-element-ref", "#/texts/3");
  await expect(selected).toBeInViewport();
  await expect(page.getByLabel("Numéro de page")).toHaveValue("2");
  await page.getByRole("tab", { name: "Markdown" }).click();
  await expect(page.locator('.pdf-page[data-current-page="true"]').getByTestId("pdf-highlight")).toHaveCount(0);
  await page.getByRole("tab", { name: /Éléments/ }).click();
  await expect(page.locator('.pdf-page[data-current-page="true"]').getByTestId("pdf-highlight")).toHaveCount(1);
});

test("empty Markdown and chunks explain missing figure enrichment", async ({
  page,
}) => {
  await page.route("**/api/documents/*/result", async (route) => {
    const response = await route.fetch();
    const result = await response.json();
    await route.fulfill({
      response,
      json: {
        ...result,
        markdown: "",
        chunks: [],
        notices: [
          "1 figure(s) conservée(s) sans classification ni OCR/VLM. Configurez le routeur de figures pour produire leur contenu textuel.",
        ],
      },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Choisir un dossier" }).click();
  await page.getByRole("button", { name: "Utiliser ce dossier" }).click();
  await page.getByRole("button", { name: /Guide.pdf/ }).click();
  const analyse = page.getByRole("button", { name: "Analyser le document" });
  if (await analyse.isVisible()) await analyse.click();
  await page.getByRole("tab", { name: /Markdown/ }).click();
  await expect(page.getByRole("status")).toContainText(
    "sans classification ni OCR/VLM",
  );
  await expect(
    page.getByText("Aucun texte extrait de ce document.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: /Chunks/ }).click();
  await expect(
    page.getByText("Aucun contenu indexable dans ce document.", {
      exact: true,
    }),
  ).toBeVisible();
});

test("result loading can be retried independently of extraction", async ({
  page,
}) => {
  let attempts = 0;
  await page.route("**/api/documents/*/result", async (route) => {
    if (attempts++ === 0)
      await route.fulfill({
        status: 503,
        json: { detail: "Résultat temporairement indisponible" },
      });
    else await route.continue();
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Choisir un dossier" }).click();
  await page.getByRole("button", { name: "Utiliser ce dossier" }).click();
  await page.getByRole("button", { name: /Guide.pdf/ }).click();
  const analyse = page.getByRole("button", { name: "Analyser le document" });
  if (await analyse.isVisible()) await analyse.click();
  await page.getByRole("button", { name: "Recharger les résultats" }).click();
  await page.getByRole("tab", { name: /Markdown/ }).click();
  await expect(page.locator(".markdown-content h1")).toHaveText("Guide PDF");
  expect(attempts).toBe(2);
});

test("recognized source text excluded from indexing is not labelled as unrecognized", async ({ page }) => {
  await page.route("**/api/documents/*/result", async (route) => {
    const response = await route.fetch();
    const result = await response.json();
    const item = result.items.find((item: { type: string }) => item.type === "text");
    item.original_text = "En-tête reconnu de la banque";
    item.content_for_llm = null;
    item.content_for_embedding = null;
    item.label = "page_header";
    await route.fulfill({ response, json: result });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Choisir un dossier" }).click();
  await page.getByRole("button", { name: "Utiliser ce dossier" }).click();
  await page.getByRole("button", { name: /Guide.pdf/ }).click();
  const analyse = page.getByRole("button", { name: "Analyser le document" });
  if (await analyse.isVisible()) await analyse.click();
  const card = page.locator(".item-card").filter({ hasText: "En-tête reconnu de la banque" });
  await expect(card).toBeVisible();
  await expect(card).not.toContainText("Texte non reconnu");
  await card.getByRole("button").first().click();
  await expect(card).toContainText("Texte reconnu, exclu du contenu indexé.");
});

test("model form applies devices and settings, polls loading, and unloads", async ({
  page,
}) => {
  const categories = ["layout", "picture_classifier", "ocr", "vlm"];
  let state: Record<string, unknown> = {
    status: "unloaded",
    configuration: null,
    requested: null,
    error: null,
    revision: 0,
  };
  let payload: Record<string, any> | null = null;
  let completeAt = 0;
  await page.route("**/api/runtime/options", async (route) => {
    const actual = await (await route.fetch()).json();
    await route.fulfill({
      json: {
        models: categories.map((category) => ({
          category,
          name: `fixture_${category}`,
          prepared: true,
          defaults: {},
          settings_schema: {},
        })),
        devices: [
          { id: "cpu", label: "CPU" },
          { id: "cuda:1", label: "GPU de test" },
        ],
        runtime: state,
        defaults: actual.defaults,
      },
    });
  });
  await page.route("**/api/runtime", async (route) => {
    const method = route.request().method();
    if (method === "POST") {
      payload = route.request().postDataJSON();
      state = {
        status: "loading",
        configuration: null,
        requested: payload,
        error: null,
        revision: 1,
      };
      completeAt = Date.now() + 700;
    } else if (method === "DELETE") {
      state = {
        status: "unloading",
        configuration: null,
        requested: null,
        error: null,
        revision: 2,
      };
      completeAt = Date.now() + 500;
    } else if (Date.now() >= completeAt && state.status === "loading") {
      state = { ...state, status: "loaded", configuration: payload };
    } else if (Date.now() >= completeAt && state.status === "unloading") {
      state = { ...state, status: "unloaded" };
    }
    await route.fulfill({ status: method === "GET" ? 200 : 202, json: state });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Paramètres", exact: true }).click();
  const modal = page.getByRole("dialog");
  await modal
    .getByLabel("Classifieur de figures (DFC)", { exact: true })
    .selectOption("fixture_picture_classifier");
  await expect(
    modal.getByRole("button", { name: "Charger et appliquer" }),
  ).toBeDisabled();
  await modal
    .getByLabel("OCR des figures", { exact: true })
    .selectOption("fixture_ocr");
  await modal
    .getByLabel("VLM des figures", { exact: true })
    .selectOption("fixture_vlm");
  await modal.getByLabel("Device VLM des figures").selectOption("cuda:1");
  const vlmOptions = modal
    .locator(".model-field")
    .filter({ has: page.locator("#model-vlm") });
  await vlmOptions.locator("summary").first().click();
  await modal
    .getByLabel("Paramètres VLM des figures", { exact: true })
    .fill('{"max_new_tokens":128}');
  await modal.getByLabel("Type de découpage").selectOption("sentence");
  await modal.getByLabel("Taille cible (caractères)").fill("256");
  await modal.getByLabel("Chevauchement (caractères)").fill("32");
  await modal.getByLabel("Minimum de phrases par chunk").fill("2");
  await modal.getByRole("button", { name: "Charger et appliquer" }).click();
  await expect(modal.getByRole("status")).toHaveText("Chargement des modèles…");
  await expect(
    modal.getByRole("button", { name: "Décharger les modèles" }),
  ).toBeDisabled();
  await expect(modal.getByRole("status")).toHaveText("Configuration chargée", { timeout: 15000 });
  expect(payload!.vlm).toEqual({
    name: "fixture_vlm",
    device: "cuda:1",
    settings: { max_new_tokens: 128 },
  });
  expect(payload!.ocr.device).toBe("cuda:1");
  expect(payload!.picture_classifier.device).toBe("cpu");
  expect(payload!.chunking).toEqual({
    kind: "sentence",
    chunk_size: 256,
    chunk_overlap: 32,
    min_sentences_per_chunk: 2,
    min_characters_per_sentence: 12,
  });
  await modal.getByRole("button", { name: "Décharger les modèles" }).click();
  await expect(modal.getByRole("status")).toHaveText(
    "Modèles déchargés · extraction native",
    { timeout: 15000 },
  );
  await page.screenshot({
    path: "/tmp/pdfparse-studio-models.png",
    fullPage: true,
  });
});

test("native mode exposes figure configuration before processing", async ({
  page,
}) => {
  await page.route("**/api/runtime", async (route) =>
    route.fulfill({
      json: {
        status: "unloaded",
        configuration: null,
        requested: null,
        error: null,
        revision: 0,
      },
    }),
  );
  await page.goto("/");
  const setup = page.locator(".figure-setup-banner");
  await expect(setup).toContainText("Figures sans OCR/VLM");
  await setup.getByRole("button", { name: "Configurer les figures" }).click();
  await expect(
    page.getByRole("dialog").getByLabel("OCR des figures", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("dialog")).toContainText(
    "Le téléchargement des poids seul n’active pas les modèles",
  );
});

test("model tags, lazy visual assets and downloadable raw RAG records", async ({ page }) => {
  let imageRequests = 0;
  await page.route("**/api/documents/*/result", async (route) => {
    const response = await route.fetch();
    const result = await response.json();
    const model = { role: "vlm", name: "fixture-visual-model", revision: "fixture-revision", device: "cpu" };
    for (const item of result.items) item.models = [model];
    result.assets = [{ id: "fixture-asset", ref: result.items[0].ref,
      asset_path: "fixture.png", caption: "Schéma hydraulique", description: "Une pompe alimente une vanne.",
      locations: result.items[0].locations, models: [model], classification: null,
      chunk_ids: result.chunks.map((chunk: { id: string }) => chunk.id), available: true,
      url: "/api/documents/fixture/assets/fixture-asset" }];
    await route.fulfill({ response, json: result });
  });
  await page.route("**/api/documents/fixture/assets/fixture-asset", async (route) => {
    imageRequests++;
    await route.fulfill({ contentType: "image/png", body: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF1cAAAAASUVORK5CYII=", "base64") });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Choisir un dossier" }).click();
  await page.getByRole("button", { name: "Utiliser ce dossier" }).click();
  await page.getByRole("button", { name: /Guide.pdf/ }).click();
  const analyse = page.getByRole("button", { name: "Analyser le document" });
  if (await analyse.isVisible()) await analyse.click();
  await expect(page.getByRole("button", { name: "Analyse terminée" })).toBeVisible({ timeout: 15000 });
  await expect(page.locator(".model-tags").first()).toContainText("fixture-visual-model");
  expect(imageRequests).toBe(0);
  const tabs = await page.getByRole("tab").allTextContents();
  expect(tabs.findIndex((text) => text.includes("Assets"))).toBeLessThan(tabs.findIndex((text) => text.includes("Chunks")));
  expect(tabs.findIndex((text) => text.includes("Brut"))).toBeGreaterThan(tabs.findIndex((text) => text.includes("Chunks")));
  await page.getByRole("tab", { name: /Assets/ }).click();
  await expect(page.getByText("Une pompe alimente une vanne.", { exact: true })).toBeVisible();
  await expect.poll(() => page.locator(".asset-image").evaluate((img: HTMLImageElement) => img.naturalWidth)).toBe(1);
  expect(imageRequests).toBe(1);
  await page.screenshot({ path: "/tmp/pdfparse-assets-tab.png", fullPage: true });
  await page.getByRole("button", { name: "Voir l’élément source" }).click();
  await expect(page.getByRole("tab", { name: /Éléments/ })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: /Brut/ }).click();
  const raw = JSON.parse((await page.locator(".raw-panel pre").textContent())!);
  expect(raw.document_path).toContain("Guide.pdf");
  expect(raw.assets).toHaveLength(1);
  expect(raw.chunks.length).toBeGreaterThan(0);
  for (const chunk of raw.chunks) {
    expect(chunk.schema_version).toBe(4);
    expect(chunk.document_path).toContain("Guide.pdf");
    expect(chunk.source_refs.length).toBeGreaterThan(0);
    expect(chunk).toHaveProperty("model_provenance");
  }
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Télécharger le JSON" }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe("Guide.pdf.chunks.json");
  expect(await download.failure()).toBeNull();
  await page.screenshot({ path: "/tmp/pdfparse-raw-tab.png", fullPage: true });
});


test('PDF.js renders CID fonts and JPEG2000 with local auxiliary resources only', async ({page}) => {
  const external: string[] = [];
  const resources: string[] = [];
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.protocol.startsWith('http') && url.hostname !== '127.0.0.1') {
      external.push(url.href);
      return route.abort();
    }
    if (url.pathname.startsWith('/pdfjs/')) resources.push(url.pathname);
    return route.continue();
  });
  await page.goto('/');
  await page.getByRole('button', {name: 'Choisir un dossier'}).click();
  await page.getByRole('button', {name: 'Utiliser ce dossier'}).click();
  for (const name of ['ZAuxiliary.pdf', 'ZJpeg2000.pdf']) {
    await page.getByRole('button', {name: new RegExp(name)}).click();
    await expect(page.locator('[data-pdf-page="1"] canvas')).toHaveAttribute('data-page', '1', {timeout: 15000});
  }
  expect(external).toEqual([]);
  await expect.poll(() => resources.some(path => path.endsWith('.bcmap'))).toBe(true);
  await expect.poll(() => resources.some(path => path.endsWith('openjpeg.wasm'))).toBe(true);
});

for (const path of ['/docs', '/redoc']) {
  test(`API documentation ${path} works without external requests`, async ({page}) => {
    const external: string[] = [];
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.protocol.startsWith('http') && url.hostname !== '127.0.0.1') {
        external.push(url.href);
        return route.abort();
      }
      return route.continue();
    });
    await page.goto(path);
    if (path === '/docs') await expect(page.locator('.swagger-ui .info .title')).toContainText('PdfParse Studio');
    else await expect(page.locator('redoc')).toContainText('PdfParse Studio');
    expect(external).toEqual([]);
  });
}

test('a folder extraction can be stopped from the jobs dialog', async ({ page }) => {
  const now = new Date().toISOString();
  const job = {
    id: 'stop-test', name: 'Analyse du dossier source', kind: 'batch',
    status: 'running', stop_requested: false, stage: 'extraction', detail: '1/2 PDF traités',
    created_at: now, started_at: now, finished_at: null as string | null, error: null,
    events: [], inferences: [], inference_count: 0, elapsed_seconds: 0, stage_seconds: {},
  };
  let started = false;
  await page.route('**/api/documents/process?*', (route) => {
    started = true;
    return route.fulfill({ status: 202, json: job });
  });
  await page.route('**/api/jobs', (route) => route.fulfill({ json: started ? [job] : [] }));
  await page.route('**/api/jobs/stop-test/stop', (route) => {
    expect(route.request().method()).toBe('POST');
    job.stop_requested = true;
    return route.fulfill({ json: job });
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Choisir un dossier' }).click();
  await page.getByRole('button', { name: 'Utiliser ce dossier' }).click();
  await page.getByRole('button', { name: "Lancer l'extraction", exact: true }).click();
  const jobs = page.getByRole('dialog');
  await jobs.getByRole('button', { name: 'Arrêter l’extraction' }).click();
  await expect(jobs.getByRole('button', { name: 'Arrêt demandé' })).toBeDisabled();
  job.status = 'cancelled';
  job.finished_at = new Date().toISOString();
  job.detail = 'Extraction arrêtée · 1/2 PDF analysés';
  await expect(jobs.locator('.job-state')).toHaveText('Arrêté', { timeout: 10000 });
  await expect(jobs.locator('.job-step')).toContainText('1/2 PDF analysés');
});
