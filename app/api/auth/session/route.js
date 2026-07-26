import { NextResponse } from "next/server";

import {
  SESSION_COOKIE,
  verifySessionToken,
} from "../../../../lib/auth.js";

export async function GET(request) {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  const session = await verifySessionToken(token);
  if (!session) {
    return NextResponse.json(
      { authenticated: false },
      {
        status: 401,
        headers: { "Cache-Control": "private, no-store, max-age=0" },
      },
    );
  }
  return NextResponse.json(
    { authenticated: true, username: session.username },
    { headers: { "Cache-Control": "private, no-store, max-age=0" } },
  );
}
