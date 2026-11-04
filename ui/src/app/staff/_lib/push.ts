"use client";

// Web Push: subscribe this phone (for its role) with the browser's push service, so alerts arrive
// as notifications even when the app is closed. The service worker (`/staff-sw.js`) shows them.

import { staffApi } from "./client";
import type { Lang } from "./types";

export type PushState = "on" | "unsupported" | "blocked" | "off" | "failed";

/** The VAPID public key (base64url) as the bytes `applicationServerKey` wants. */
export function keyBytes(base64url: string): Uint8Array {
  const padded =
    base64url.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob(padded);
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

function sameKey(a: ArrayBuffer | null, b: Uint8Array): boolean {
  if (!a) return false;
  const x = new Uint8Array(a);
  return x.length === b.length && x.every((v, i) => v === b[i]);
}

/** Subscribe (or refresh the role of) this phone. Never asks for permission itself. */
export async function enablePush(role: string, lang: Lang): Promise<PushState> {
  if (
    typeof window === "undefined" ||
    !("serviceWorker" in navigator) ||
    !("PushManager" in window)
  ) {
    return "unsupported";
  }
  if (typeof Notification === "undefined") return "unsupported";
  if (Notification.permission === "denied") return "blocked";
  if (Notification.permission !== "granted") return "off";
  try {
    const { public_key } = await staffApi.pushKey();
    const key = keyBytes(public_key);
    const registration = await navigator.serviceWorker.ready;
    let subscription = await registration.pushManager.getSubscription();
    if (subscription && !sameKey(subscription.options.applicationServerKey, key)) {
      await subscription.unsubscribe(); // the site's key changed: subscribe again
      subscription = null;
    }
    subscription ??= await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: key,
    });
    await staffApi.pushSubscribe(role, lang, subscription.toJSON());
    return "on";
  } catch {
    return "failed";
  }
}
