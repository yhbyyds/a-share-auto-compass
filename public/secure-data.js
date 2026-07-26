(() => {
  const STORAGE_KEY = "a_share_v11_access_key";
  const ENCRYPTED_URL = "/data/forecast.enc.json?v=1.7.0";
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  function decodeBase64Url(value) {
    const base64 = value.replaceAll("-", "+").replaceAll("_", "/");
    const padded = base64 + "=".repeat((4 - base64.length % 4) % 4);
    const binary = atob(padded);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  }

  function encodeBase64Url(bytes) {
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary)
      .replaceAll("+", "-")
      .replaceAll("/", "_")
      .replaceAll("=", "");
  }

  async function encryptedPayload() {
    const response = await fetch(ENCRYPTED_URL, { cache: "no-store" });
    if (!response.ok) throw new Error("加密预测文件读取失败");
    return response.json();
  }

  async function deriveKey(password, payload) {
    const material = await crypto.subtle.importKey(
      "raw",
      encoder.encode(password),
      "PBKDF2",
      false,
      ["deriveKey"],
    );
    return crypto.subtle.deriveKey(
      {
        name: "PBKDF2",
        hash: "SHA-256",
        salt: decodeBase64Url(payload.salt),
        iterations: payload.iterations,
      },
      material,
      { name: "AES-GCM", length: 256 },
      true,
      ["decrypt"],
    );
  }

  async function importKey(encoded) {
    return crypto.subtle.importKey(
      "raw",
      decodeBase64Url(encoded),
      { name: "AES-GCM", length: 256 },
      false,
      ["decrypt"],
    );
  }

  async function decrypt(payload, key) {
    const plaintext = await crypto.subtle.decrypt(
      {
        name: "AES-GCM",
        iv: decodeBase64Url(payload.iv),
      },
      key,
      decodeBase64Url(payload.ciphertext),
    );
    return JSON.parse(decoder.decode(plaintext));
  }

  async function unlock(username, password) {
    if (String(username).trim() !== "admin") {
      throw new Error("账号或密码不正确");
    }
    try {
      const payload = await encryptedPayload();
      const key = await deriveKey(String(password), payload);
      const data = await decrypt(payload, key);
      const rawKey = new Uint8Array(await crypto.subtle.exportKey("raw", key));
      sessionStorage.setItem(STORAGE_KEY, encodeBase64Url(rawKey));
      return data;
    } catch {
      sessionStorage.removeItem(STORAGE_KEY);
      throw new Error("账号或密码不正确");
    }
  }

  async function load() {
    const encodedKey = sessionStorage.getItem(STORAGE_KEY);
    if (!encodedKey) throw new Error("LOGIN_REQUIRED");
    try {
      const [payload, key] = await Promise.all([
        encryptedPayload(),
        importKey(encodedKey),
      ]);
      return await decrypt(payload, key);
    } catch {
      sessionStorage.removeItem(STORAGE_KEY);
      throw new Error("LOGIN_REQUIRED");
    }
  }

  function logout() {
    sessionStorage.removeItem(STORAGE_KEY);
  }

  window.SecureForecast = { load, logout, unlock };
})();
