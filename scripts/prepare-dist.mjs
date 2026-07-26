import { cp, mkdir, rm } from "node:fs/promises";
import { resolve } from "node:path";

const source = resolve(".openai", "hosting.json");
const targetDirectory = resolve("dist", ".openai");
const target = resolve(targetDirectory, "hosting.json");

await mkdir(targetDirectory, { recursive: true });
await cp(source, target);

const privateForecastCopies = [
  resolve("dist", "client", "data", "forecast.json"),
  resolve("dist", "standalone", "dist", "client", "data", "forecast.json"),
  resolve("dist", "standalone", "public", "data", "forecast.json"),
];
await Promise.all(
  privateForecastCopies.map((path) => rm(path, { force: true })),
);

console.log("Copied Sites metadata into dist/");
console.log("Removed public forecast copies from the Sites build.");
