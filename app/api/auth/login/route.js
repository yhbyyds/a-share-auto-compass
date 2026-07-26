import { NextResponse } from "next/server";

import {
  authenticate,
  createSessionToken,
  SESSION_COOKIE,
  SESSION_MAX_AGE_SECONDS,
} from "../../../../lib/auth.js";
import {
  clearFailures,
  rateLimitStatus,
  recordFailure,
} from "../../../../lib/rate-limit.js";

function clientKey(request) {
  return (
    request.headers.get("cf-connecting-ip")
    || request.headers.get("x-forwarded-for")?.split(",")[0]?.trim()
    || "unknown"
  );
}

function sameOrigin(request) {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  try {
    return new URL(origin).host === new URL(request.url).host;
  } catch {
    return false;
  }
}

export async function POST(request) {
  if (!sameOrigin(request)) {
    return NextResponse.json({ error: "请求来源无效" }, { status: 403 });
  }
  if (!request.headers.get("content-type")?.includes("application/json")) {
    return NextResponse.json({ error: "请求格式无效" }, { status: 415 });
  }

  const key = clientKey(request);
  const status = rateLimitStatus(key);
  if (!status.allowed) {
    return NextResponse.json(
      { error: "尝试次数过多，请稍后再试" },
      {
        status: 429,
        headers: { "Retry-After": String(status.retryAfter) },
      },
    );
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "请求内容无效" }, { status: 400 });
  }
  const username = String(body.username || "").slice(0, 80);
  const password = String(body.password || "").slice(0, 256);
  if (!await authenticate(username, password)) {
    const failed = recordFailure(key);
    const response = NextResponse.json(
      { error: "账号或密码不正确" },
      { status: 401 },
    );
    if (!failed.allowed) {
      response.headers.set("Retry-After", String(failed.retryAfter));
    }
    return response;
  }

  clearFailures(key);
  const token = await createSessionToken(username);
  const response = NextResponse.json({ ok: true });
  response.cookies.set({
    name: SESSION_COOKIE,
    value: token,
    httpOnly: true,
    secure: process.env.AUTH_COOKIE_SECURE !== "false",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE_SECONDS,
  });
  response.headers.set("Cache-Control", "no-store");
  return response;
}
