import { NextResponse } from "next/server";

import {
  SESSION_COOKIE,
  verifySessionToken,
} from "../../../lib/auth.js";
import forecast from "../../../public/data/forecast.json" with { type: "json" };

const PRIVATE_HEADERS = {
  "Cache-Control": "private, no-store, max-age=0",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
};

export async function GET(request) {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  const session = await verifySessionToken(token);
  if (!session) {
    return NextResponse.json(
      { error: "登录已失效" },
      { status: 401, headers: PRIVATE_HEADERS },
    );
  }
  return NextResponse.json(forecast, { headers: PRIVATE_HEADERS });
}
