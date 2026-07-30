(() => {
  const STORAGE_KEY = "a_share_v11_access_key";
  const PERSONAL_SESSION_KEY = "a_share_personal_session_key_v1";
  const PERSONAL_STORAGE_KEY = "a_share_personal_portfolio_v1";
  const DEVICE_KEY = "a_share_personal_device_key_v1";
  const ENCRYPTED_URL = "/data/forecast.enc.json?v=1.8.0";
  const PUBLISHED_FORECAST_URL = "https://yhbyyds.github.io/a-share-auto-compass/data/forecast.json?v=1.18.0";
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
    if (!response.ok) throw new Error("encrypted forecast read failed");
    return response.json();
  }

  async function publishedForecast() {
    const response = await fetch(PUBLISHED_FORECAST_URL, { cache: "no-store" });
    if (!response.ok) throw new Error("published forecast read failed");
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
      ["encrypt", "decrypt"],
    );
  }

  async function derivePersonalKey(password) {
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
        salt: encoder.encode("a-share-auto-compass-personal-v1"),
        iterations: 310000,
      },
      material,
      { name: "AES-GCM", length: 256 },
      true,
      ["encrypt", "decrypt"],
    );
  }

  async function importKey(encoded, usages = ["encrypt", "decrypt"]) {
    return crypto.subtle.importKey(
      "raw",
      decodeBase64Url(encoded),
      { name: "AES-GCM", length: 256 },
      false,
      usages,
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
      const personalKey = await derivePersonalKey(String(password));
      const rawPersonalKey = new Uint8Array(
        await crypto.subtle.exportKey("raw", personalKey),
      );
      sessionStorage.setItem(STORAGE_KEY, encodeBase64Url(rawKey));
      sessionStorage.setItem(
        PERSONAL_SESSION_KEY,
        encodeBase64Url(rawPersonalKey),
      );
      try {
        return await publishedForecast();
      } catch {
        return data;
      }
    } catch {
      sessionStorage.removeItem(STORAGE_KEY);
      sessionStorage.removeItem(PERSONAL_SESSION_KEY);
      throw new Error("账号或密码不正确");
    }
  }

  async function load() {
    const encodedKey = sessionStorage.getItem(STORAGE_KEY);
    if (!encodedKey) throw new Error("LOGIN_REQUIRED");
    try {
      return await publishedForecast();
    } catch {
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
  }

  function logout() {
    sessionStorage.removeItem(STORAGE_KEY);
    sessionStorage.removeItem(PERSONAL_SESSION_KEY);
  }

  async function personalKey() {
    const secureHost = window.location.hostname.endsWith(".chatgpt.site");
    const sessionKey = sessionStorage.getItem(PERSONAL_SESSION_KEY);
    if (sessionKey) return importKey(sessionKey);
    if (secureHost) throw new Error("PERSONAL_LOGIN_REQUIRED");

    let deviceKey = localStorage.getItem(DEVICE_KEY);
    if (!deviceKey) {
      deviceKey = encodeBase64Url(crypto.getRandomValues(new Uint8Array(32)));
      localStorage.setItem(DEVICE_KEY, deviceKey);
    }
    return importKey(deviceKey);
  }

  async function savePersonalData(value) {
    const key = await personalKey();
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const plaintext = encoder.encode(JSON.stringify(value));
    const ciphertext = await crypto.subtle.encrypt(
      {
        name: "AES-GCM",
        iv,
        additionalData: encoder.encode("portfolio-v1"),
      },
      key,
      plaintext,
    );
    localStorage.setItem(
      PERSONAL_STORAGE_KEY,
      JSON.stringify({
        schema: 1,
        iv: encodeBase64Url(iv),
        ciphertext: encodeBase64Url(new Uint8Array(ciphertext)),
      }),
    );
  }

  async function loadPersonalData() {
    const stored = localStorage.getItem(PERSONAL_STORAGE_KEY);
    if (!stored) return null;
    const payload = JSON.parse(stored);
    if (payload.schema !== 1) throw new Error("PERSONAL_SCHEMA_UNSUPPORTED");
    const key = await personalKey();
    const plaintext = await crypto.subtle.decrypt(
      {
        name: "AES-GCM",
        iv: decodeBase64Url(payload.iv),
        additionalData: encoder.encode("portfolio-v1"),
      },
      key,
      decodeBase64Url(payload.ciphertext),
    );
    return JSON.parse(decoder.decode(plaintext));
  }

  function clearPersonalData() {
    localStorage.removeItem(PERSONAL_STORAGE_KEY);
  }

  function personalStorageMode() {
    return window.location.hostname.endsWith(".chatgpt.site")
      ? "account"
      : "device";
  }

  window.SecureForecast = {
    load,
    logout,
    unlock,
    savePersonalData,
    loadPersonalData,
    clearPersonalData,
    personalStorageMode,
  };
})();
