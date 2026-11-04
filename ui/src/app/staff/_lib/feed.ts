"use client";

// The live feed: server-sent events from `/staff/stream`, with polling and the last known feed
// (kept on the phone) when the stream is down. New items are reported once to `onNew`, and the
// phone tells the API when it first showed each one, so alert → phone latency is measured.

import { useCallback, useEffect, useRef, useState } from "react";

import { staffApi, streamUrl } from "./client";
import type { StaffItem } from "./types";

export type Connection = "live" | "reconnecting" | "offline";

const CACHE_KEY = "rulecam.staff.feed";
const POLL_MS = 5000;
const WATCHDOG_MS = 40_000;

interface Envelope {
  items?: StaffItem[];
  item?: StaffItem;
  sent_at?: string;
}

/** Newest first; one entry per item. */
export function mergeItems(current: StaffItem[], incoming: StaffItem[]): StaffItem[] {
  const byId = new Map(current.map((item) => [item.item_id, item]));
  for (const item of incoming) byId.set(item.item_id, item);
  return [...byId.values()].sort((a, b) => Date.parse(b.ts) - Date.parse(a.ts));
}

/** Items the role still has to act on come first, then the rest, each newest first. */
export function ordered(items: StaffItem[]): StaffItem[] {
  const open = (i: StaffItem) => i.status !== "done" && i.status !== "false_alarm";
  return [...items.filter(open), ...items.filter((i) => !open(i))];
}

function readCache(role: string | null): StaffItem[] {
  try {
    const raw = localStorage.getItem(`${CACHE_KEY}.${role ?? "*"}`);
    return raw ? (JSON.parse(raw) as StaffItem[]) : [];
  } catch {
    return [];
  }
}

function writeCache(role: string | null, items: StaffItem[]): void {
  try {
    localStorage.setItem(`${CACHE_KEY}.${role ?? "*"}`, JSON.stringify(items.slice(0, 50)));
  } catch {
    // private mode or full storage: the phone just keeps nothing offline
  }
}

export interface FeedState {
  items: StaffItem[];
  connection: Connection;
  upsert: (item: StaffItem) => void;
  refresh: () => Promise<void>;
}

export function useStaffFeed(
  role: string | null,
  enabled: boolean,
  onNew: (item: StaffItem, latencyMs: number) => void,
): FeedState {
  const [items, setItems] = useState<StaffItem[]>([]);
  const [connection, setConnection] = useState<Connection>("reconnecting");
  const known = useRef<Set<string>>(new Set());
  const onNewRef = useRef(onNew);
  onNewRef.current = onNew;

  const accept = useCallback(
    (incoming: StaffItem[], announce: boolean) => {
      const fresh: StaffItem[] = [];
      for (const item of incoming) {
        if (!known.current.has(item.item_id)) {
          known.current.add(item.item_id);
          fresh.push(item);
        }
      }
      setItems((current) => {
        const next = mergeItems(current, incoming);
        writeCache(role, next);
        return next;
      });
      for (const item of fresh) {
        const latency = Date.now() - Date.parse(item.ts);
        if (role && announce)
          void staffApi.seen(item.item_id, role, Math.max(0, latency)).catch(() => {});
        if (announce && item.status === "open") onNewRef.current(item, latency);
      }
    },
    [role],
  );

  const refresh = useCallback(async () => {
    try {
      accept(await staffApi.feed(role), true);
      setConnection((c) => (c === "offline" ? "reconnecting" : c));
    } catch {
      setConnection("offline");
    }
  }, [accept, role]);

  useEffect(() => {
    if (!enabled) return undefined;
    known.current = new Set();
    const cached = readCache(role);
    setItems(cached);
    let source: EventSource | null = null;
    let poll: number | null = null;
    let closed = false;

    const startPolling = () => {
      if (poll !== null) return;
      poll = window.setInterval(() => void refresh(), POLL_MS);
    };
    const stopPolling = () => {
      if (poll !== null) window.clearInterval(poll);
      poll = null;
    };

    // A stream that silently stops (a proxy holding a dead upstream) is reopened: the API
    // sends a `ping` every 15 s of quiet, so 40 s of silence means the connection is gone.
    let last = Date.now();
    let watchdog: number | null = null;
    const touch = () => {
      last = Date.now();
    };
    const open = () => {
      source?.close();
      source = new EventSource(streamUrl(role));
      source.addEventListener("snapshot", (event) => {
        touch();
        const data = JSON.parse((event as MessageEvent<string>).data) as Envelope;
        setItems([]);
        accept(data.items ?? [], false);
        setConnection("live");
        stopPolling();
      });
      source.addEventListener("item", (event) => {
        touch();
        const data = JSON.parse((event as MessageEvent<string>).data) as Envelope;
        if (data.item) accept([data.item], true);
        setConnection("live");
      });
      source.addEventListener("ping", touch);
      source.onerror = () => {
        if (closed) return;
        setConnection("reconnecting");
        startPolling();
        if (source?.readyState === EventSource.CLOSED) {
          window.setTimeout(() => {
            if (!closed) open();
          }, 3000);
        }
      };
    };

    if (typeof EventSource === "undefined") {
      void refresh();
      startPolling();
    } else {
      open();
      watchdog = window.setInterval(() => {
        if (Date.now() - last < WATCHDOG_MS) return;
        touch();
        setConnection("reconnecting");
        open();
      }, 5000);
    }
    return () => {
      closed = true;
      source?.close();
      stopPolling();
      if (watchdog !== null) window.clearInterval(watchdog);
    };
  }, [accept, enabled, refresh, role]);

  const upsert = useCallback(
    (item: StaffItem) => {
      known.current.add(item.item_id);
      setItems((current) => {
        const next = mergeItems(current, [item]);
        writeCache(role, next);
        return next;
      });
    },
    [role],
  );

  return { items, connection, upsert, refresh };
}
