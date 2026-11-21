import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? walk(join(dir, entry.name)) : [join(dir, entry.name)],
  );
}
let count = 0;
for (const file of walk(join(root, "src"))) {
  if (file.endsWith(".js")) {
    const result = spawnSync(process.execPath, ["--check", file], {
      encoding: "utf8",
    });
    if (result.status) throw new Error(result.stderr);
    count++;
  }
  if (
    file.endsWith(".jsx") &&
    /dangerouslySetInnerHTML|<iframe[^>]+src=["']https?:/i.test(
      readFileSync(file, "utf8"),
    )
  ) {
    throw new Error(`Inline executable content: ${file}`);
  }
}
console.log(
  `${count} modules JavaScript valides ; composants JSX sans injection HTML brute.`,
);
