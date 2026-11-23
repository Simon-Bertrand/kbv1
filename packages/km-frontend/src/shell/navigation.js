export function initialDestination(location) {
  const hash = location.hash.replace(/^#/, "");
  if (
    ["athena", "sources", "rag", "traces", "library", "evaluation", "atlas", "ingestion"].includes(hash)
  ) {
    return { view: hash };
  }
  const path = location.pathname.replace(/\/$/, "") || "/";
  if (path.startsWith("/viewer/")) return { view: "viewer" };
  return (
    {
      "/atlas": { view: "atlas" },
      "/ingestion": { view: "ingestion" },
      "/documents": { view: "sources" },
      "/metadata": { view: "library", tab: "metadata" },
      "/kanban": { view: "library" },
      "/management": { view: "library" },
      "/sandbox": { view: "rag" },
    }[path] || { view: "athena" }
  );
}
