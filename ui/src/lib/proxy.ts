// Request headers the UI's middleware sends on to the API through the `/rulecam-api` rewrite.
// Behind Vercel and the RunPod proxy the API sees a proxy's address, so every browser would share
// one rate limit (logins, worker disputes). With RULECAM_PROXY_SECRET set (a server-only Vercel
// variable, equal to UI_PROXY_SECRET on the API pod), the browser's address goes along with the
// secret; the API trusts the address only next to that secret. Copies of either header sent by
// the browser itself are always dropped, so a visitor cannot pick the address it is counted as.

export const CLIENT_IP_HEADER = "x-rulecam-client-ip";
export const PROXY_SECRET_HEADER = "x-rulecam-proxy-secret";

/** The browser's address as the edge saw it (Vercel sets `x-real-ip` and `x-forwarded-for`). */
export function clientIp(incoming: Headers): string {
  const forwarded = incoming.get("x-forwarded-for")?.split(",")[0];
  return (incoming.get("x-real-ip") ?? forwarded ?? "").trim();
}

export function proxyHeaders(incoming: Headers, secret: string | undefined): Headers {
  const headers = new Headers(incoming);
  headers.delete(CLIENT_IP_HEADER);
  headers.delete(PROXY_SECRET_HEADER);
  const ip = clientIp(incoming);
  if (secret && ip) {
    headers.set(CLIENT_IP_HEADER, ip);
    headers.set(PROXY_SECRET_HEADER, secret);
  }
  return headers;
}
