/* Rulecam Staff service worker (scope /staff).
 *
 * - Offline shell: /staff and the built JS/CSS it loads are cached, so the app opens with no
 *   signal and shows the last feed it saw (kept by the page itself).
 * - Navigations are network-first (a new build wins), static assets cache-first.
 * - API calls (/rulecam-api/...) always go to the network: alerts are never served stale here.
 * - Notifications the page shows while in the background open the app on that alert.
 */

const VERSION = "rulecam-staff-v1";
const SHELL = [
  "/staff",
  "/staff-manifest.webmanifest",
  "/staff-icon-192.png",
  "/staff-icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(VERSION)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

function isStatic(url) {
  return (
    url.pathname.startsWith("/_next/static/") ||
    /^\/staff-[\w.-]+\.(png|webmanifest)$/.test(url.pathname)
  );
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/rulecam-api/")) return;

  if (request.mode === "navigate" && url.pathname.startsWith("/staff")) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(VERSION).then((cache) => cache.put("/staff", copy));
          return response;
        })
        .catch(() => caches.match("/staff").then((hit) => hit || Response.error())),
    );
    return;
  }

  if (isStatic(url)) {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(VERSION).then((cache) => cache.put(request, copy));
            }
            return response;
          }),
      ),
    );
  }
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/staff";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if (client.url.includes("/staff") && "focus" in client) return client.focus();
      }
      return self.clients.openWindow(target);
    }),
  );
});
