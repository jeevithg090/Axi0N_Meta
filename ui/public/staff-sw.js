/* Rulecam Staff service worker (scope /staff).
 *
 * - Offline shell: /staff and the built JS/CSS it loads are cached, so the app opens with no
 *   signal and shows the last feed it saw (kept by the page itself).
 * - Navigations are network-first (a new build wins), static assets cache-first.
 * - API calls (/rulecam-api/...) always go to the network: alerts are never served stale here.
 * - Web Push: an alert pushed for this phone's role is shown as a notification with the
 *   verifier's verdict, even when the app is closed. "Acknowledge" opens the item and
 *   acknowledges it (`/staff?item=<id>&ack=1`); tapping the notification just opens it.
 */

const VERSION = "rulecam-staff-v2";
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

self.addEventListener("push", (event) => {
  let message = {};
  try {
    message = event.data ? event.data.json() : {};
  } catch {
    message = { title: "Rulecam alert", body: event.data ? event.data.text() : "" };
  }
  const real = Boolean(message.item_id) && message.item_id !== "push-test";
  const urgent = message.severity === "high" || message.severity === "critical";
  event.waitUntil(
    self.registration.showNotification(message.title || "Rulecam alert", {
      body: message.body || "",
      tag: message.item_id || "rulecam",
      renotify: true,
      requireInteraction: urgent,
      icon: "/staff-icon-192.png",
      badge: "/staff-icon-192.png",
      vibrate: urgent ? [300, 120, 300, 120, 300] : [200, 100, 200],
      timestamp: message.ts ? Date.parse(message.ts) : Date.now(),
      data: { url: message.url || "/staff", item_id: message.item_id || null },
      actions: real
        ? [
            { action: "ack", title: "Acknowledge" },
            { action: "open", title: "Open" },
          ]
        : [],
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  let target = data.url || "/staff";
  if (event.action === "ack" && data.item_id)
    target += (target.includes("?") ? "&" : "?") + "ack=1";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if (client.url.includes("/staff") && "focus" in client) {
          return client.navigate(target).then((c) => (c || client).focus());
        }
      }
      return self.clients.openWindow(target);
    }),
  );
});
