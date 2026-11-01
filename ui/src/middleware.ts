import { NextResponse, type NextRequest } from "next/server";

import { proxyHeaders } from "./lib/proxy";

/** Adds the browser's address (with the shared secret) to API calls; see `lib/proxy.ts`. */
export function middleware(request: NextRequest) {
  const headers = proxyHeaders(request.headers, process.env.RULECAM_PROXY_SECRET);
  return NextResponse.next({ request: { headers } });
}

export const config = { matcher: "/rulecam-api/:path*" };
