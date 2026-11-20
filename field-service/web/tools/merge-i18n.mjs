// Deep-merge a patch into src/i18n/<lang>.json (dev helper; kept out of the build).
import fs from "node:fs";
const [lang, patchPath] = process.argv.slice(2);
const file = `src/i18n/${lang}.json`;
const base = JSON.parse(fs.readFileSync(file, "utf8"));
const patch = JSON.parse(fs.readFileSync(patchPath, "utf8"));
const merge = (a, b) => { for (const [k, v] of Object.entries(b)) a[k] = v && typeof v === "object" && !Array.isArray(v) ? merge(a[k] ?? {}, v) : v; return a; };
fs.writeFileSync(file, JSON.stringify(merge(base, patch), null, 2) + "\n");
