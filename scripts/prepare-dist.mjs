import { randomBytes, webcrypto } from "node:crypto";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const password = process.env.FORECAST_ACCESS_PASSWORD;
if (!password || password.length < 16) {
  throw new Error(
    "FORECAST_ACCESS_PASSWORD must contain at least 16 characters",
  );
}

const source = resolve(".openai", "hosting.json");
const targetDirectory = resolve("dist", ".openai");
const target = resolve(targetDirectory, "hosting.json");

await mkdir(targetDirectory, { recursive: true });
await cp(source, target);

const salt = randomBytes(18);
const iv = randomBytes(12);
const material = await webcrypto.subtle.importKey(
  "raw",
  new TextEncoder().encode(password),
  "PBKDF2",
  false,
  ["deriveKey"],
);
const key = await webcrypto.subtle.deriveKey(
  {
    name: "PBKDF2",
    hash: "SHA-256",
    salt,
    iterations: 310_000,
  },
  material,
  { name: "AES-GCM", length: 256 },
  false,
  ["encrypt"],
);
const plaintext = await readFile(resolve("public", "data", "forecast.json"));
const ciphertext = new Uint8Array(
  await webcrypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plaintext),
);
const encryptedPayload = JSON.stringify({
  version: 1,
  cipher: "AES-256-GCM",
  kdf: "PBKDF2-SHA256",
  iterations: 310_000,
  salt: Buffer.from(salt).toString("base64url"),
  iv: Buffer.from(iv).toString("base64url"),
  ciphertext: Buffer.from(ciphertext).toString("base64url"),
});
const encryptedForecastCopies = [
  resolve("dist", "client", "data", "forecast.enc.json"),
  resolve("dist", "standalone", "dist", "client", "data", "forecast.enc.json"),
  resolve("dist", "standalone", "public", "data", "forecast.enc.json"),
];
await Promise.all(
  encryptedForecastCopies.map(async (path) => {
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, encryptedPayload, "utf8");
  }),
);

const privateForecastCopies = [
  resolve("dist", "client", "data", "forecast.json"),
  resolve("dist", "standalone", "dist", "client", "data", "forecast.json"),
  resolve("dist", "standalone", "public", "data", "forecast.json"),
];
await Promise.all(
  privateForecastCopies.map((path) => rm(path, { force: true })),
);

console.log("Copied Sites metadata into dist/");
console.log("Encrypted forecast data and removed plaintext Sites copies.");
