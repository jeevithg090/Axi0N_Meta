import type { NextConfig } from "next";

import { API_BASE, API_PROXY } from "./src/lib/config";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The browser calls the API through this origin: the API sends no CORS headers.
  async rewrites() {
    return [{ source: `${API_PROXY}/:path*`, destination: `${API_BASE}/:path*` }];
  },
};

export default nextConfig;
