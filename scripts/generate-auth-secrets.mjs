import { randomBytes } from "node:crypto";

import { hashPassword } from "../lib/auth.js";

const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%";
const randomPassword = Array.from(
  randomBytes(20),
  (byte) => alphabet[byte % alphabet.length],
).join("");
const sessionSecret = randomBytes(32).toString("base64url");
const passwordHash = await hashPassword(randomPassword);

process.stdout.write(JSON.stringify({
  username: "admin",
  password: randomPassword,
  passwordHash,
  sessionSecret,
}));
