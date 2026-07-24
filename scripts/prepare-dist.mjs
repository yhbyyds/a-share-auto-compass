import { cp, mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const source = resolve(".openai", "hosting.json");
const targetDirectory = resolve("dist", ".openai");
const target = resolve(targetDirectory, "hosting.json");

await mkdir(targetDirectory, { recursive: true });
await cp(source, target);

console.log("Copied Sites metadata into dist/");
