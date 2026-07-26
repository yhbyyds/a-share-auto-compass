import { randomBytes, webcrypto } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const password = process.env.FORECAST_ACCESS_PASSWORD;
if (!password || password.length < 16) {
  throw new Error(
    "FORECAST_ACCESS_PASSWORD must contain at least 16 characters",
  );
}

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

await writeFile(
  resolve("public", "data", "forecast.enc.json"),
  encryptedPayload,
  "utf8",
);
console.log("Created temporary encrypted forecast asset.");
