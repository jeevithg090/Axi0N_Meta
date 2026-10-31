// Where the Rulecam API lives. Override with NEXT_PUBLIC_RULECAM_API (e.g. the tunnel URL).

export const API_BASE = (process.env.NEXT_PUBLIC_RULECAM_API ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

/** The WebSocket URL for `/ws`, derived from the API base. */
export function wsUrl(apiBase: string = API_BASE): string {
  return apiBase.replace(/^http/, "ws") + "/ws";
}
