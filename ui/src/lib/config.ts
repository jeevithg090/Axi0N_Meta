// Where the Rulecam API lives. Override with NEXT_PUBLIC_RULECAM_API (e.g. the tunnel URL).

export const API_BASE = (process.env.NEXT_PUBLIC_RULECAM_API ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

/** The WebSocket URL for `/ws`, derived from the API base. */
export function wsUrl(apiBase: string = API_BASE): string {
  return apiBase.replace(/^http/, "ws") + "/ws";
}

/**
 * Same-origin prefix that `next.config.ts` rewrites to the API, so plain HTTP calls (`GET /mode`)
 * work from the browser without CORS on the API.
 */
export const API_PROXY = "/rulecam-api";
