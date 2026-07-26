import { webcrypto } from "node:crypto";
import { access, readFile } from "node:fs/promises";
import { resolve } from "node:path";

const password = process.env.FORECAST_ACCESS_PASSWORD;
if (!password) throw new Error("FORECAST_ACCESS_PASSWORD is required");

const payload = JSON.parse(
  await readFile(
    resolve("dist", "client", "data", "forecast.enc.json"),
    "utf8",
  ),
);
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
    salt: Buffer.from(payload.salt, "base64url"),
    iterations: payload.iterations,
  },
  material,
  { name: "AES-GCM", length: 256 },
  false,
  ["decrypt"],
);
const plaintext = await webcrypto.subtle.decrypt(
  {
    name: "AES-GCM",
    iv: Buffer.from(payload.iv, "base64url"),
  },
  key,
  Buffer.from(payload.ciphertext, "base64url"),
);
const forecast = JSON.parse(new TextDecoder().decode(plaintext));

const plaintextCopies = [
  resolve("dist", "client", "data", "forecast.json"),
  resolve("dist", "standalone", "dist", "client", "data", "forecast.json"),
  resolve("dist", "standalone", "public", "data", "forecast.json"),
];
for (const path of plaintextCopies) {
  try {
    await access(path);
    throw new Error(`Plaintext forecast still exists: ${path}`);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

console.log(
  `Encrypted build verified: release=${forecast.meta.release} `
  + `version=${forecast.meta.version}`,
);
