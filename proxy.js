import { NextResponse } from "next/server";

import {
  isAuthConfigured,
  SESSION_COOKIE,
  verifySessionToken,
} from "./lib/auth.js";

const PUBLIC_PATHS = new Set([
  "/login",
  "/login.css",
  "/api/auth/login",
  "/api/auth/logout",
]);

function securityHeaders(response) {
  response.headers.set("Cache-Control", "private, no-store, max-age=0");
  response.headers.set("Referrer-Policy", "no-referrer");
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("X-Frame-Options", "DENY");
  response.headers.set(
    "Permissions-Policy",
    "camera=(), microphone=(), geolocation=()",
  );
  return response;
}

export async function proxy(request) {
  const { pathname, search } = request.nextUrl;
  if (
    PUBLIC_PATHS.has(pathname)
    || pathname.startsWith("/_next/")
    || pathname.startsWith("/favicon")
  ) {
    return securityHeaders(NextResponse.next());
  }

  if (!isAuthConfigured()) {
    return securityHeaders(
      NextResponse.json(
        { error: "站点认证尚未完成配置" },
        { status: 503 },
      ),
    );
  }

  const token = request.cookies.get(SESSION_COOKIE)?.value;
  const session = await verifySessionToken(token);
  if (session) {
    return securityHeaders(NextResponse.next());
  }

  if (pathname.startsWith("/api/")) {
    return securityHeaders(
      NextResponse.json({ error: "登录已失效" }, { status: 401 }),
    );
  }

  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = "/login";
  const destination = `${pathname}${search}`;
  if (destination !== "/") loginUrl.searchParams.set("next", destination);
  return securityHeaders(NextResponse.redirect(loginUrl));
}

export const config = {
  matcher: ["/((?!_next/static|_next/image).*)"],
};
