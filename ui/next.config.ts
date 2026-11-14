import type { NextConfig } from "next";

import { API_BASE, API_PROXY } from "./src/lib/config";

// Where the Next.js server forwards `/rulecam-api/*`. RULECAM_API_URL is for a server that reaches
// the API on another address than the browser does (docker compose: http://rulecam-api:8000);
// without it the proxy uses the browser's API base.
const PROXY_TARGET = (process.env.RULECAM_API_URL ?? API_BASE).replace(/\/$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The browser calls the API through this origin: the API sends no CORS headers.
  async rewrites() {
    return [
      { source: `${API_PROXY}/:path*`, destination: `${PROXY_TARGET}/:path*` },
      // The static verify page (public/verify/): `/verify/` is redirected to `/verify`, where
      // its `./chain.js` import resolves to `/chain.js`, so serve both from public/verify/.
      { source: "/verify", destination: "/verify/index.html" },
      { source: "/chain.js", destination: "/verify/chain.js" },
    ];
  },
};

export default nextConfig;
