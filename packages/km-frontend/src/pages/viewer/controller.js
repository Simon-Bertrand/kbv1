import { imageError } from "../../shared/image-error.js";
import { createLifecycle } from "../../shared/lifecycle.js";
export function mountPage(root = document) {
  const scope = createLifecycle();
  const { setTimeout, setInterval, requestAnimationFrame } = scope;

  const $ = (id) => root.querySelector(`#${CSS.escape(id)}`);
  const body = root.host || document.body;
  const styleRoot = root.host || document.documentElement;
  const ns = "http://www.w3.org/2000/svg";
  let doc = null,
    pages = [],
    page = null,
    chosen = null,
    index = 0,
    requestVersion = 0;
  const documentId = decodeURIComponent(location.pathname.split("/").pop());
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function svg(tag, attrs = {}) {
    const e = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attrs))
      e.setAttribute(key, String(value));
    return e;
  }
  async function json(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) {
      let reason = "Consultation indisponible (" + r.status + ").";
      try {
        reason = (await r.json()).detail || reason;
      } catch {}
      throw Error(reason);
    }
    return r.json();
  }
  function label(locator) {
    if (Array.isArray(locator.pages) && locator.pages.length > 1) return "Pages " + locator.pages.join(", ");
    if (locator.type === "chunk" && !locator.page) return "Fragment";
    if (locator.type === "visual" && !locator.page) return "Figure";
    return locator.page
      ? "Page " + locator.page
      : locator.sheet
        ? "Feuille " + locator.sheet
        : locator.type === "message"
          ? "Message"
          : locator.type === "archive"
            ? "Archive"
            : "Texte / unité source";
  }
  function shown() {
    return (page?.blocks || []).filter(
      (b) =>
        ($("nested").checked || b.level === 0) &&
        (!$("type").value || b.source_type === $("type").value),
    );
  }
  function preview(b) {
    $("crop").replaceChildren();
    $("cropNote").textContent = "";
    if (b.crop_url) {
      const image = el("img");
      image.alt = "Crop enregistré par MinerU : " + b.source_type;
      image.src = b.crop_url;
      image.onerror = () => renderCrop(b);
      $("crop").append(image);
      $("cropNote").textContent = "Crop enregistré par MinerU.";
    } else renderCrop(b);
  }
  function renderCrop(b) {
    $("crop").replaceChildren();
    if (page.image_url && page.geometry_available && b.bbox_valid) {
      const [x1, y1, x2, y2] = b.bbox;
      const crop = svg("svg", {
        viewBox: `${x1} ${y1} ${x2 - x1} ${y2 - y1}`,
        role: "img",
        "aria-label": "Découpage visuel du rendu de page : " + b.source_type,
      });
      crop.append(
        svg("image", {
          href: page.image_url,
          width: page.width,
          height: page.height,
          preserveAspectRatio: "none",
        }),
      );
      $("crop").append(crop);
      $("cropNote").textContent =
        "Découpage visuel du rendu existant selon la bbox. Aucun fichier crop supplémentaire créé.";
    } else {
      $("crop").append(
        el(
          "p",
          "blank",
          "Aucun crop ni rendu avec coordonnées valides pour ce bloc.",
        ),
      );
      $("cropNote").textContent = "Le texte extrait reste consultable.";
    }
  }
  function choose(key) {
    chosen = page.blocks.find((b) => b.key === key);
    if (!chosen) return;
    document
      .querySelectorAll("[data-key]")
      .forEach((e) => e.classList.toggle("active", e.dataset.key === key));
    $("cropTitle").textContent = "Bloc " + key + " · " + chosen.source_type;
    preview(chosen);
    $("details").textContent = `Type MinerU : ${chosen.source_type}
Type enregistré : ${chosen.type}
Ordre de lecture : ${chosen.reading_order ?? "absent"}
Niveau interne : ${chosen.level}
BBox : ${JSON.stringify(chosen.bbox ?? null)}
Repère : ${page.coordinate_space || "non fourni"}
Dimensions source : ${page.width ?? "—"} × ${page.height ?? "—"}
Coordonnées valides : ${chosen.bbox_valid ? "oui" : "non / absentes"}`;
    $("text").textContent = chosen.text || "(aucun texte extrait)";
    if (chosen.text_truncated)
      $("text").textContent += `
[Texte tronqué pour cet affichage.]`;
  }
  function render() {
    if (!page) return;
    const list = shown();
    $("sheet").replaceChildren();
    $("blocks").replaceChildren();
    sizePage();
    $("sheet").classList.toggle("no-labels", !$("labels").checked);
    const valid = list.filter((b) => b.bbox_valid);
    $("counts").textContent =
      `${list.length} bloc(s) · ${valid.length} bbox valides`;
    if (page.image_url && page.geometry_available) {
      const canvas = svg("svg", {
        viewBox: `0 0 ${page.width} ${page.height}`,
        role: "group",
        "aria-label": "Page avec boîtes détectées",
      });
      const image = svg("image", {
        href: page.image_url,
        width: page.width,
        height: page.height,
        preserveAspectRatio: "none",
      });
      const renderedPage = page;
      image.addEventListener("error", async () => {
        const reason = await imageError(renderedPage.image_url);
        if (page === renderedPage) $("notice").textContent = reason;
      });
      canvas.append(image);
      for (const b of valid) {
        const [x1, y1, x2, y2] = b.bbox;
        const g = svg("g", {
          class: "region",
          tabindex: 0,
          role: "button",
          "aria-label": "Bloc " + b.key + " · " + b.source_type,
        });
        g.dataset.key = b.key;
        g.append(
          svg("rect", {
            class: "box",
            x: x1,
            y: y1,
            width: x2 - x1,
            height: y2 - y1,
          }),
        );
        const text = b.key + " · " + b.source_type;
        const labelWidth = Math.min(page.width, text.length * 4.5 + 6);
        const lx = Math.max(0, Math.min(x1, page.width - labelWidth));
        const ly = Math.min(page.height - 2, y2 + 9);
        g.append(
          svg("rect", {
            class: "label-bg",
            x: lx,
            y: ly - 8,
            width: labelWidth,
            height: 10,
          }),
        );
        const caption = svg("text", { class: "box-label", x: lx + 2, y: ly });
        caption.textContent = text;
        g.append(caption);
        g.onclick = () => {
          choose(b.key);
          if (body.classList.contains("reading")) toggleDetails(true);
        };
        g.onkeydown = (e) => {
          if (["Enter", " "].includes(e.key)) {
            e.preventDefault();
            choose(b.key);
            if (body.classList.contains("reading")) toggleDetails(true);
          }
        };
        canvas.append(g);
      }
      $("sheet").append(canvas);
    } else if (page.image_url) {
      const image = el("img");
      image.src = page.image_url;
      image.alt = "Rendu de la page";
      const renderedPage = page;
      image.onerror = async () => {
        const reason = await imageError(renderedPage.image_url);
        if (page === renderedPage) $("notice").textContent = reason;
      };
      image.style.width = "100%";
      $("sheet").append(
        image,
        el("p", "blank", "Dimensions absentes : aucune boîte superposée."),
      );
    } else {
      $("sheet").append(
        el(
          "div",
          "blank",
          page.render_state?.message || "Cette unité ne dispose pas de rendu de page. Consulter les blocs textuels ci-contre, les pièces jointes ci-dessous ou télécharger l’original.",
        ),
      );
    }
    for (const b of list) {
      const button = el("button", "block-item", b.key + " · " + b.source_type);
      button.dataset.key = b.key;
      button.append(
        el(
          "small",
          "",
          b.bbox_valid
            ? "bbox " + b.bbox.join(", ")
            : "Coordonnées absentes ou invalides",
        ),
      );
      button.onclick = () => choose(b.key);
      $("blocks").append(button);
    }
    if (!list.length)
      $("blocks").append(el("p", "blank", "Aucun bloc pour ce filtre."));
    if (chosen && list.some((b) => b.key === chosen.key)) choose(chosen.key);
    else if (list.length) choose(list[0].key);
    else {
      chosen = null;
      $("crop").replaceChildren();
      $("details").textContent = "";
      $("text").textContent = "";
      $("cropNote").textContent = "";
    }
    $("method").textContent =
      `${label(page.locator)} · ${page.extraction_method || "extraction native"} · ${page.coordinate_space || "sans repère de page"}${page.gaps.length ? " · Limites : " + page.gaps.join(", ") : ""}`;
  }
  async function loadPage() {
    const version = ++requestVersion;
    $("previous").disabled = index === 0;
    $("next").disabled = index >= pages.length - 1;
    $("pages").value = String(index);
    $("notice").textContent = "Chargement de la page…";
    try {
      const result = await json(
        "/view/pages/" + encodeURIComponent(pages[index].id),
      );
      if (version !== requestVersion) return;
      page = result;
      chosen = null;
      $("type").replaceChildren(new Option("Tous les types", ""));
      for (const type of [
        ...new Set(page.blocks.map((b) => b.source_type)),
      ].sort())
        $("type").append(new Option(type, type));
      $("notice").textContent = "";
      render();
    } catch (e) {
      if (version === requestVersion) $("notice").textContent = e.message;
    }
  }
  async function start() {
    try {
      let offset = 0;
      do {
        const batch = await json(
          "/view/documents/" +
            encodeURIComponent(documentId) +
            "/pages?offset=" +
            offset +
            "&limit=200",
        );
        doc = batch.document;
        pages.push(...batch.items);
        offset += batch.items.length;
        if (offset >= batch.total || !batch.items.length) break;
      } while (true);
      $("name").textContent = doc.observed?.original_name || documentId;
      document.title = "RAG · " + $("name").textContent;
      $("chain").textContent =
        doc.provenance?.source_chain?.map((x) => x.name).join(" → ") ||
        doc.observed?.relative_path ||
        documentId;
      $("source").href =
        "/view/documents/" + encodeURIComponent(documentId) + "/source";
      pages.forEach((p, i) =>
        $("pages").append(new Option(label(p.locator), String(i))),
      );
      for (const child of doc.children || []) {
        const item =
          child.document_id && child.status !== "removed"
            ? el("a", "", child.name + " · " + child.status)
            : el("span", "", child.name + " · " + child.status);
        if (item.tagName === "A")
          item.href = "/viewer/" + encodeURIComponent(child.document_id);
        $("children").append(item);
      }
      if (pages.length) {
        const requested = new URLSearchParams(location.search).get("page_id");
        const found = pages.findIndex((p) => p.id === requested);
        if (requested && found < 0)
          $("notice").textContent = "Page demandée absente de ce document.";
        if (requested && found < 0) {
          $("sheet").append(
            el(
              "div",
              "blank",
              "Page demandée introuvable. Choisir une page dans la liste.",
            ),
          );
        } else {
          index = Math.max(0, found);
          await loadPage();
        }
      } else {
        $("notice").textContent =
          "Aucune page extraite pour ce document ; état : " +
          (doc.status || "inconnu");
        $("sheet").append(
          el(
            "div",
            "blank",
            "L’extraction n’a pas produit de page consultable.",
          ),
        );
        $("previous").disabled = true;
        $("next").disabled = true;
      }
    } catch (e) {
      $("notice").textContent = e.message;
      $("name").textContent = "Document indisponible";
    }
  }
  function sizePage() {
    const reader = $("reader");
    const toolbar = root.querySelector(".toolbar");
    styleRoot.style.setProperty(
      "--toolbar-height",
      toolbar.offsetHeight + "px",
    );
    const height = Math.max(
      240,
      window.innerHeight - reader.getBoundingClientRect().top - 18,
    );
    styleRoot.style.setProperty("--reader-height", height + "px");
    const availableWidth = Math.max(100, reader.clientWidth - 22);
    const availableHeight = Math.max(100, reader.clientHeight - 22);
    if ($("zoom").value === "fit" && page?.geometry_available) {
      $("sheet").style.width =
        Math.floor(
          Math.min(
            availableWidth,
            (availableHeight * page.width) / page.height,
          ),
        ) + "px";
    } else if ($("zoom").value === "fit") $("sheet").style.width = "100%";
    else
      $("sheet").style.width =
        (availableWidth * Number($("zoom").value)) / 100 + "px";
  }
  function toggleDetails(show) {
    body.classList.toggle("show-details", show);
    $("detailsToggle").setAttribute("aria-pressed", String(show));
  }
  $("fullscreen").onclick = () => {
    const enabled = !body.classList.contains("reading");
    body.classList.toggle("reading", enabled);
    toggleDetails(false);
    $("fullscreen").textContent = enabled
      ? "Quitter le plein écran"
      : "Plein écran";
    $("fullscreen").setAttribute("aria-pressed", String(enabled));
    $("reader").scrollTop = 0;
    sizePage();
  };
  $("detailsToggle").onclick = () =>
    toggleDetails(!body.classList.contains("show-details"));
  scope.listen(root, "keydown", (e) => {
    if (e.key === "Escape") {
      if (body.classList.contains("show-details")) toggleDetails(false);
      else if (body.classList.contains("reading")) $("fullscreen").click();
    }
  });
  scope.listen(window, "resize", sizePage);
  const resizeObserver = new ResizeObserver(sizePage);
  resizeObserver.observe(root.querySelector(".toolbar"));
  $("pages").onchange = () => {
    index = Number($("pages").value);
    loadPage();
  };
  $("previous").onclick = () => {
    if (index > 0) {
      index--;
      loadPage();
    }
  };
  $("next").onclick = () => {
    if (index < pages.length - 1) {
      index++;
      loadPage();
    }
  };
  for (const id of ["type", "nested", "labels", "zoom"])
    $(id).onchange = render;
  start();

  return () => {
    scope.dispose();
    resizeObserver.disconnect();
    body.classList.remove("reading", "show-details");
  };
}
