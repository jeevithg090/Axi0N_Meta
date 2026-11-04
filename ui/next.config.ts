import type { NextConfig } from "next";

import { API_BASE, API_PROXY } from "./src/lib/config";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The browser calls the API through this origin: the API sends no CORS headers.
  async rewrites() {
    return [
      { source: `${API_PROXY}/:path*`, destination: `${API_BASE}/:path*` },
      // The static verify page (public/verify/): `/verify/` is redirected to `/verify`, where
      // its `./chain.js` import resolves to `/chain.js`, so serve both from public/verify/.
      { source: "/verify", destination: "/verify/index.html" },
      { source: "/chain.js", destination: "/verify/chain.js" },
    ];
  },
};

export default nextConfig;
