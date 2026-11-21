// Read the image endpoint error without downloading a successful binary twice.
export async function imageError(url) {
  const fallback = "Le service fournit une image que le navigateur ne peut pas afficher. Vérifier le format du fichier.";
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (response.ok) { await response.body?.cancel(); return fallback; }
    const type = response.headers.get("content-type") || "";
    if (type.includes("json")) {
      const data = await response.json();
      if (typeof data.detail === "string") return data.detail;
    } else await response.body?.cancel();
    return `Le chargement de l’image a échoué (HTTP ${response.status}).`;
  } catch { return "Le service de rendu est injoignable. Le texte extrait reste consultable."; }
}
