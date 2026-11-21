import { Marked } from "marked";
import DOMPurify from "dompurify";

const renders = new WeakMap();
let diagramLibrary;
let sequence = 0;
const escape = (text) => String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function mathToken(source, block, formulas) {
  for (const [left, right] of block ? [["$$", "$$"], ["\\[", "\\]"]] : [["$", "$"], ["\\(", "\\)"]]) {
    if (!source.startsWith(left) || (!block && source.startsWith("$$"))) continue;
    if (left === "$" && /\s/.test(source[1] || "")) continue;
    let end = source.indexOf(right, left.length);
    while (end >= 0) {
      let backslashes = 0;
      for (let index = end - 1; index >= 0 && source[index] === "\\"; index--) backslashes++;
      if (backslashes % 2 === 0 && !(right === "$" && /\d/.test(source[end + 1] || ""))) break;
      end = source.indexOf(right, end + right.length);
    }
    if (end < 0) continue;
    const text = source.slice(left.length, end);
    if (!text.trim() || (!block && text.includes("\n")) || formulas.length >= 100) continue;
    const index = formulas.push({ text, display: block }) - 1;
    return { type: block ? "athenaMathBlock" : "athenaMathInline", raw: source.slice(0, end + right.length), index };
  }
}

export function parseMarkdown(markdown) {
  const formulas = [];
  const parser = new Marked({ gfm: true, breaks: false, renderer: {
    html: ({ text }) => escape(text),
    code: ({ text, lang }) => `<pre><code class="language-${escape((lang || "").split(/\s/)[0])}">${escape(text)}</code></pre>`,
  } });
  parser.use({ extensions: [
    { name: "athenaMathBlock", level: "block", start: (src) => src.search(/\$\$|\\\[/),
      tokenizer: (src) => mathToken(src, true, formulas),
      renderer: (token) => `<div class="math-display" data-athena-math="${token.index}"></div>` },
    { name: "athenaMathInline", level: "inline", start: (src) => src.search(/\$|\\\(/),
      tokenizer: (src) => mathToken(src, false, formulas),
      renderer: (token) => `<span class="math-inline" data-athena-math="${token.index}"></span>` },
  ] });
  return { html: parser.parse(String(markdown).slice(0, 120000)), formulas };
}

export function sanitizedMarkdown(markdown) {
  const { html, formulas } = parseMarkdown(markdown);
  const fragment = DOMPurify.sanitize(html, { RETURN_DOM_FRAGMENT: true, USE_PROFILES: { html: true },
    FORBID_TAGS: ["img", "svg", "math", "iframe", "object", "embed", "style", "form", "input"],
    FORBID_ATTR: ["style", "srcset"] });
  for (const link of fragment.querySelectorAll("a")) {
    const href = link.getAttribute("href") || "";
    if (!/^(https?:\/\/|mailto:|#|\/(?!\/))/i.test(href)) link.removeAttribute("href");
    link.setAttribute("rel", "noopener noreferrer");
  }
  return { fragment, formulas };
}

async function renderDiagram(code) {
  if (/%%\{|^\s*---/.test(code) || code.length > 20000 || code.split("\n").length > 300)
    throw Error("Diagram configuration or size refused");
  diagramLibrary ||= import("mermaid").then(({ default: mermaid }) => {
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", htmlLabels: false,
      suppressErrorRendering: true, maxTextSize: 20000, maxEdges: 200,
      flowchart: { htmlLabels: false },
      secure: ["secure", "securityLevel", "startOnLoad", "maxTextSize", "maxEdges", "htmlLabels", "flowchart", "themeCSS"] });
    return mermaid;
  });
  const mermaid = await diagramLibrary;
  return (await mermaid.render("athenaDiagram"+(++sequence), code)).svg;
}

export async function renderMarkdown(host, markdown, { diagramRenderer = renderDiagram } = {}) {
  const token = {};
  renders.set(host, token);
  const current = () => renders.get(host) === token;
  const { fragment, formulas } = sanitizedMarkdown(markdown);
  host.replaceChildren(fragment);
  host.classList.add("markdown-body");
  for (const paragraph of host.querySelectorAll("p"))
    if (paragraph.textContent.startsWith("Sources :")) paragraph.classList.add("answer-citations");
  for (const table of host.querySelectorAll("table")) {
    const wrapper = document.createElement("div");
    wrapper.className = "markdown-table";
    table.replaceWith(wrapper);
    wrapper.append(table);
  }
  if (formulas.length) {
    const { default: katex } = await import("katex");
    if (!current()) return;
    for (const element of host.querySelectorAll("[data-athena-math]")) {
      const formula = formulas[Number(element.dataset.athenaMath)];
      if (!formula) continue;
      if (formula.text.length > 4000) { element.textContent = formula.text; continue; }
      katex.render(formula.text, element, { displayMode: formula.display, throwOnError: false,
        trust: false, maxExpand: 1000, maxSize: 20, strict: "ignore" });
      element.removeAttribute("data-athena-math");
    }
  }
  for (const code of host.querySelectorAll("code.language-mermaid")) {
    if (!current()) return;
    const text = code.textContent;
    const figure = document.createElement("figure");
    figure.className = "mermaid-figure";
    const diagram = document.createElement("div");
    diagram.className = "mermaid-diagram";
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Voir le code Mermaid";
    const pre = document.createElement("pre");
    pre.textContent = text;
    details.append(summary, pre);
    figure.append(diagram, details);
    code.parentElement.replaceWith(figure);
    try {
      const svg = await diagramRenderer(text);
      if (!current()) return;
      const clean = DOMPurify.sanitize(svg, { RETURN_DOM_FRAGMENT: true,
        USE_PROFILES: { svg: true, svgFilters: true }, FORBID_TAGS: ["foreignObject", "script", "image", "a"] });
      for (const item of clean.querySelectorAll("*")) {
        for (const name of ["href", "xlink:href"]) {
          const value = item.getAttribute(name);
          if (value && !value.startsWith("#")) item.removeAttribute(name);
        }
      }
      diagram.replaceChildren(clean);
    } catch {
      if (!current()) return;
      diagram.textContent = "Ce schéma ne peut pas être rendu. Son code reste consultable ci-dessous.";
      diagram.classList.add("diagram-error");
      details.open = true;
    }
  }
}
