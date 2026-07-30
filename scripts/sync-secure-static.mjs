import { cp, mkdir } from "node:fs/promises";
import { resolve } from "node:path";

// The protected site keeps its encrypted login snapshot in dist/data.  Copying
// only these UI assets updates every page renderer without overwriting that
// snapshot or placing the plaintext forecast in the protected artifact.
const sourceDir = resolve("public");
const targets = [
  resolve("dist", "client"),
  resolve("dist", "standalone", "dist", "client"),
];
const assets = [
  ".nojekyll",
  "app.js",
  "index.html",
  "login.css",
  "login.html",
  "login.js",
  "portfolio.js",
  "secure-data.js",
  "styles.css",
];

for (const target of targets) {
  await mkdir(target, { recursive: true });
  for (const asset of assets) {
    await cp(resolve(sourceDir, asset), resolve(target, asset));
  }
}

console.log(`Synced ${assets.length} protected-site UI assets to ${targets.length} build targets.`);
