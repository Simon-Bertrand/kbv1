export function locatorLabel(locator = {}) {
  if (Number.isInteger(locator.page) && locator.page > 0) return `Page ${locator.page}`;
  if (Array.isArray(locator.pages) && locator.pages.length) return `Pages ${locator.pages.join(", ")}`;
  if (locator.type === "chunk") return "Fragment";
  if (locator.type === "visual") return "Figure";
  if (locator.sheet) return `Feuille ${locator.sheet}${locator.range ? ` · ${locator.range}` : ""}`;
  if (locator.type === "message") return "Message";
  if (locator.type === "text") return "Texte";
  if (locator.type === "metadata") return "Métadonnées";
  if (locator.type === "markdown") return `Markdown · lignes ${locator.start_line}–${locator.end_line}`;
  if (locator.type === "archive") return "Archive";
  return "Document";
}

export function sourceName(source, names = new Map()) {
  return source.original_name || source.observed?.original_name || names.get(source.document_id) || "Document sans nom";
}
