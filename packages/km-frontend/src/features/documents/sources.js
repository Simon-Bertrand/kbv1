const collator = new Intl.Collator("fr", { numeric: true, sensitivity: "base" });
export function orderedEntries(entries, query = "") {
  const text = query.trim().toLocaleLowerCase("fr");
  return (Array.isArray(entries)?entries:[]).filter(entry => typeof entry.name === "string" && ["file", "directory"].includes(entry.type) && entry.name.toLocaleLowerCase("fr").includes(text))
    .sort((a,b) => Number(b.type === "directory") - Number(a.type === "directory") || collator.compare(a.name,b.name));
}
export function fileSize(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "—";
  if (!value) return "0 o";
  const units = ["o","Kio","Mio","Gio","Tio"];
  const index = Math.min(Math.floor(Math.log(value)/Math.log(1024)), units.length-1);
  return (value/1024**index).toLocaleString("fr-FR",{maximumFractionDigits:1})+" "+units[index];
}
export function fileLink(path) {
  return "/api/files/raw?path="+encodeURIComponent(path);
}
