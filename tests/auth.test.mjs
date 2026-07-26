import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import test from "node:test";

import {
  authenticate,
  createSessionToken,
  hashPassword,
  verifyPassword,
  verifySessionToken,
} from "../lib/auth.js";

test("PBKDF2 password records accept only the original password", async () => {
  const record = await hashPassword("correct horse battery staple");
  assert.equal(
    await verifyPassword("correct horse battery staple", record),
    true,
  );
  assert.equal(await verifyPassword("wrong password", record), false);
});

test("authentication checks both username and password", async () => {
  process.env.AUTH_USERNAME = "admin";
  process.env.AUTH_PASSWORD_HASH = await hashPassword("temporary-password");
  process.env.AUTH_SESSION_SECRET = randomBytes(32).toString("base64url");

  assert.equal(await authenticate("admin", "temporary-password"), true);
  assert.equal(await authenticate("other", "temporary-password"), false);
  assert.equal(await authenticate("admin", "wrong"), false);
});

test("session tokens reject expiry and tampering", async () => {
  process.env.AUTH_USERNAME = "admin";
  process.env.AUTH_PASSWORD_HASH = await hashPassword("temporary-password");
  process.env.AUTH_SESSION_SECRET = randomBytes(32).toString("base64url");
  const now = Date.now();
  const token = await createSessionToken("admin", now);

  assert.equal((await verifySessionToken(token, now)).username, "admin");
  assert.equal(
    await verifySessionToken(token, now + 13 * 60 * 60 * 1000),
    null,
  );
  assert.equal(
    await verifySessionToken(`${token.slice(0, -1)}x`, now),
    null,
  );
});
