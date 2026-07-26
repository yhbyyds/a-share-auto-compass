const encoder = new TextEncoder();

export const SESSION_COOKIE = "a_share_session";
export const SESSION_MAX_AGE_SECONDS = 12 * 60 * 60;

function encodeBase64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

function decodeBase64Url(value) {
  const base64 = value.replaceAll("-", "+").replaceAll("_", "/");
  const padded = base64 + "=".repeat((4 - base64.length % 4) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function constantTimeEqual(left, right) {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left[index] ^ right[index];
  }
  return difference === 0;
}

function requireConfig() {
  const username = process.env.AUTH_USERNAME;
  const passwordHash = process.env.AUTH_PASSWORD_HASH;
  const sessionSecret = process.env.AUTH_SESSION_SECRET;
  if (!username || !passwordHash || !sessionSecret) {
    throw new Error("认证环境变量未完整配置");
  }
  return { username, passwordHash, sessionSecret };
}

export function isAuthConfigured() {
  return Boolean(
    process.env.AUTH_USERNAME
    && process.env.AUTH_PASSWORD_HASH
    && process.env.AUTH_SESSION_SECRET,
  );
}

async function derivePassword(password, salt, iterations, byteLength) {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    {
      name: "PBKDF2",
      hash: "SHA-256",
      salt,
      iterations,
    },
    key,
    byteLength * 8,
  );
  return new Uint8Array(bits);
}

export async function hashPassword(
  password,
  { iterations = 310_000, salt } = {},
) {
  const actualSalt = salt || crypto.getRandomValues(new Uint8Array(18));
  const derived = await derivePassword(
    password,
    actualSalt,
    iterations,
    32,
  );
  return [
    "pbkdf2_sha256",
    iterations,
    encodeBase64Url(actualSalt),
    encodeBase64Url(derived),
  ].join("$");
}

export async function verifyPassword(password, record) {
  try {
    const [scheme, iterationsText, saltText, expectedText] = record.split("$");
    if (scheme !== "pbkdf2_sha256") return false;
    const iterations = Number(iterationsText);
    if (!Number.isInteger(iterations) || iterations < 210_000) return false;
    const salt = decodeBase64Url(saltText);
    const expected = decodeBase64Url(expectedText);
    const actual = await derivePassword(
      password,
      salt,
      iterations,
      expected.length,
    );
    return constantTimeEqual(actual, expected);
  } catch {
    return false;
  }
}

async function hmac(message, secretText) {
  const key = await crypto.subtle.importKey(
    "raw",
    decodeBase64Url(secretText),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return new Uint8Array(
    await crypto.subtle.sign("HMAC", key, encoder.encode(message)),
  );
}

export async function createSessionToken(username, now = Date.now()) {
  const { sessionSecret } = requireConfig();
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const payload = {
    username,
    issued_at: Math.floor(now / 1000),
    expires_at: Math.floor(now / 1000) + SESSION_MAX_AGE_SECONDS,
    nonce: encodeBase64Url(nonce),
  };
  const payloadText = encodeBase64Url(encoder.encode(JSON.stringify(payload)));
  const signature = encodeBase64Url(await hmac(payloadText, sessionSecret));
  return `${payloadText}.${signature}`;
}

export async function verifySessionToken(token, now = Date.now()) {
  if (!token || !token.includes(".")) return null;
  try {
    const { username, sessionSecret } = requireConfig();
    const [payloadText, signatureText] = token.split(".");
    const expectedSignature = await hmac(payloadText, sessionSecret);
    const suppliedSignature = decodeBase64Url(signatureText);
    if (!constantTimeEqual(expectedSignature, suppliedSignature)) return null;
    const payload = JSON.parse(
      new TextDecoder().decode(decodeBase64Url(payloadText)),
    );
    if (payload.username !== username) return null;
    if (!Number.isInteger(payload.expires_at)) return null;
    if (payload.expires_at <= Math.floor(now / 1000)) return null;
    return payload;
  } catch {
    return null;
  }
}

export async function authenticate(username, password) {
  if (!isAuthConfigured()) return false;
  const config = requireConfig();
  const supplied = encoder.encode(String(username || ""));
  const expected = encoder.encode(config.username);
  const usernameMatches = constantTimeEqual(supplied, expected);
  const passwordMatches = await verifyPassword(
    String(password || ""),
    config.passwordHash,
  );
  return usernameMatches && passwordMatches;
}
