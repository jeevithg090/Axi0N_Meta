"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { errorText } from "@/lib/api";

import { buzz, notifyInBackground, readAloud, tone } from "../_lib/alerting";
import { staffApi } from "../_lib/client";
import { ordered, useStaffFeed } from "../_lib/feed";
import { enablePush, type PushState } from "../_lib/push";
import { LANG_NAMES, WORDS, roleWords } from "../_lib/i18n";
import type { RoleInfo, StaffAction, StaffConfig, StaffItem } from "../_lib/types";
import styles from "../staff.module.css";
import { AskDock } from "./AskDock";
import { ItemCard } from "./ItemCard";
import { Onboarding, type Setup } from "./Onboarding";
import { DoneSheet, EscalateSheet } from "./Sheets";
import { StatsView } from "./StatsView";
import { icons } from "@/components/icons";

const SETUP_KEY = "rulecam.staff.setup";
const MAX_TRANSLATED = 6;

function loadSetup(): Setup | null {
  try {
    const raw = localStorage.getItem(SETUP_KEY);
    return raw ? (JSON.parse(raw) as Setup) : null;
  } catch {
    return null;
  }
}

function saveSetup(setup: Setup): void {
  try {
    localStorage.setItem(SETUP_KEY, JSON.stringify(setup));
  } catch {
    // private mode: the phone asks again next time
  }
}

type Sheet = { kind: "done" | "escalate"; item: StaffItem } | null;

export function StaffApp() {
  const [setup, setSetup] = useState<Setup | null>(null);
  const [ready, setReady] = useState(false);
  const [editing, setEditing] = useState(false);
  const [config, setConfig] = useState<StaffConfig | null>(null);
  const [roles, setRoles] = useState<RoleInfo[]>([]);
  const [tab, setTab] = useState<"feed" | "stats">("feed");
  const [everyone, setEveryone] = useState(false);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [messages, setMessages] = useState<Record<string, string>>({});
  const [translated, setTranslated] = useState<Record<string, string>>({});
  const [sheet, setSheet] = useState<Sheet>(null);
  const asked = useRef<Set<string>>(new Set());
  const [push, setPush] = useState<PushState>("off");
  // A notification's deep link: `/staff?item=<id>` opens the item, `&ack=1` also acknowledges it.
  const deepLink = useRef<{ item: string | null; ack: boolean; done: boolean }>({
    item: null,
    ack: false,
    done: false,
  });

  useEffect(() => {
    setSetup(loadSetup());
    setReady(true);
    const query = new URLSearchParams(window.location.search);
    deepLink.current = { item: query.get("item"), ack: query.get("ack") === "1", done: false };
    staffApi
      .config()
      .then(setConfig)
      .catch(() => setConfig(null));
    staffApi
      .roles()
      .then(setRoles)
      .catch(() => setRoles([]));
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/staff-sw.js", { scope: "/staff" }).catch(() => {});
    }
  }, []);

  const lang = setup?.lang ?? "en";
  const role = setup?.role ?? null;
  const words = WORDS[lang];

  const onNew = useCallback(
    (item: StaffItem) => {
      setFresh((f) => new Set(f).add(item.item_id));
      window.setTimeout(
        () =>
          setFresh((f) => {
            const next = new Set(f);
            next.delete(item.item_id);
            return next;
          }),
        3000,
      );
      buzz(item.severity);
      tone();
      void notifyInBackground(item);
      if (setup?.autoRead && role) {
        void readAloud(item, lang, role).then((line) => {
          if (lang !== "en") setTranslated((t) => ({ ...t, [item.item_id]: line.text }));
        });
      }
    },
    [lang, role, setup?.autoRead],
  );

  const feed = useStaffFeed(everyone ? null : role, Boolean(setup) && !editing, onNew);

  // Web Push: once notifications are allowed, this phone receives its role's alerts when closed.
  useEffect(() => {
    if (!role || editing || !config?.push) return;
    void enablePush(role, lang).then(setPush);
  }, [config?.push, editing, lang, role]);

  // Open (and, from the notification's Acknowledge button, acknowledge) the linked item.
  useEffect(() => {
    const link = deepLink.current;
    if (link.done || !link.item || !role) return;
    const item = feed.items.find((i) => i.item_id === link.item);
    if (!item) return;
    link.done = true;
    window.history.replaceState(null, "", "/staff");
    if (link.ack && item.status === "open") {
      setBusy(item.item_id);
      staffApi
        .act(item.item_id, "acknowledge", role)
        .then((result) => {
          feed.upsert(result.item);
          setMessages((m) => ({ ...m, [item.item_id]: result.did }));
        })
        .catch(() => {})
        .finally(() => setBusy(null));
    }
    setFresh((f) => new Set(f).add(item.item_id));
    window.setTimeout(() => {
      document.querySelector(`[data-item="${CSS.escape(item.item_id)}"]`)?.scrollIntoView({
        block: "start",
        behavior: "smooth",
      });
    }, 50);
  }, [feed, role]);

  // Each open card's line in the chosen language (Muse Spark writes it; cached on the server).
  useEffect(() => {
    if (lang === "en" || !role) return;
    const todo = feed.items
      .filter(
        (i) =>
          i.status !== "done" &&
          i.status !== "false_alarm" &&
          !asked.current.has(`${lang}:${i.item_id}`),
      )
      .slice(0, MAX_TRANSLATED);
    for (const item of todo) {
      asked.current.add(`${lang}:${item.item_id}`);
      staffApi
        .speech(item.item_id, lang, role)
        .then((line) => setTranslated((t) => ({ ...t, [item.item_id]: line.text })))
        .catch(() => {});
    }
  }, [feed.items, lang, role]);

  const say = (itemId: string, text: string) => setMessages((m) => ({ ...m, [itemId]: text }));

  const run = async (item: StaffItem, action: StaffAction, extra: { to_role?: string } = {}) => {
    if (!role) return;
    setBusy(item.item_id);
    try {
      const result = await staffApi.act(item.item_id, action, role, extra);
      feed.upsert(result.item);
      say(item.item_id, result.did);
    } catch (error) {
      say(item.item_id, errorText(error, "Not sent"));
    } finally {
      setBusy(null);
    }
  };

  const onAct = (item: StaffItem, action: StaffAction) => {
    if (action === "done") setSheet({ kind: "done", item });
    else if (action === "escalate") setSheet({ kind: "escalate", item });
    else void run(item, action);
  };

  const finishDone = async (item: StaffItem, photo: File | null, note: string) => {
    setSheet(null);
    if (!role) return;
    setBusy(item.item_id);
    try {
      const result = await staffApi.done(item.item_id, role, photo, note);
      feed.upsert(result.item);
      say(item.item_id, result.did);
    } catch (error) {
      say(item.item_id, errorText(error, "Not sent"));
    } finally {
      setBusy(null);
    }
  };

  if (!ready) return null;
  if (!setup || editing) {
    return (
      <div className={styles.wrap}>
        <Onboarding
          initial={setup ?? {}}
          onDone={(next) => {
            saveSetup(next);
            setSetup(next);
            setEditing(false);
          }}
        />
      </div>
    );
  }

  const items = ordered(feed.items);
  const openCount = items.filter((i) => i.status !== "done" && i.status !== "false_alarm").length;
  const dot =
    feed.connection === "live"
      ? styles.dotLive
      : feed.connection === "offline"
        ? styles.dotBad
        : styles.dotWarn;

  return (
    <div className={styles.wrap}>
      <header className={styles.top}>
        <span className={styles.brand}>
          <span className={styles.brandMark} aria-hidden>
            ▲
          </span>
          {words.appName}
        </span>
        {push === "on" ? (
          <span className={styles.chip} title={words.pushOn} aria-label={words.pushOn}>
            {icons.bell}
          </span>
        ) : null}
        <span className={styles.chip} aria-live="polite">
          <span className={`${styles.dot} ${dot}`} />
          {feed.connection === "live"
            ? words.live
            : feed.connection === "offline"
              ? "Offline"
              : "…"}
        </span>
        <button
          type="button"
          className={styles.chip}
          onClick={() => setEditing(true)}
          aria-label={words.changeRole}
        >
          {roleWords(setup.role)} · {LANG_NAMES[lang].slice(0, 2).toUpperCase()}
        </button>
      </header>

      <nav className={styles.tabs}>
        <button
          type="button"
          className={`${styles.tab} ${tab === "feed" ? styles.tabOn : ""}`}
          onClick={() => setTab("feed")}
        >
          {words.feed} {openCount ? `(${openCount})` : ""}
        </button>
        <button
          type="button"
          className={`${styles.tab} ${tab === "stats" ? styles.tabOn : ""}`}
          onClick={() => setTab("stats")}
        >
          {words.stats}
        </button>
      </nav>

      {feed.connection === "offline" ? (
        <div className={`${styles.banner} ${styles.bannerBad}`}>{words.offline}</div>
      ) : null}
      {config?.dry_run ? <div className={styles.banner}>{words.dryRun}</div> : null}

      {tab === "stats" ? (
        <StatsView words={words} />
      ) : (
        <div className={styles.list}>
          <label className={styles.toggle}>
            <input
              type="checkbox"
              checked={everyone}
              onChange={(e) => setEveryone(e.target.checked)}
            />
            {words.everyone}
          </label>
          {items.length === 0 ? (
            <div className={styles.empty}>
              <div className={styles.emptyTitle}>{words.nothingOpen}</div>
              {words.nothingOpenHint}
            </div>
          ) : null}
          {items.map((item) => (
            <ItemCard
              key={item.item_id}
              item={item}
              words={words}
              fresh={fresh.has(item.item_id)}
              busy={busy === item.item_id}
              translated={lang === "en" ? null : (translated[item.item_id] ?? null)}
              message={messages[item.item_id] ?? null}
              onAct={onAct}
              onRead={(i) =>
                void readAloud(i, lang, setup.role).then((line) => {
                  if (lang !== "en") setTranslated((t) => ({ ...t, [i.item_id]: line.text }));
                })
              }
            />
          ))}
        </div>
      )}

      <AskDock
        role={setup.role}
        lang={lang}
        words={words}
        config={config}
        onTasks={(made) => made.forEach(feed.upsert)}
      />

      {sheet?.kind === "done" ? (
        <DoneSheet
          item={sheet.item}
          words={words}
          onClose={() => setSheet(null)}
          onDone={(photo, note) => void finishDone(sheet.item, photo, note)}
        />
      ) : null}
      {sheet?.kind === "escalate" ? (
        <EscalateSheet
          item={sheet.item}
          words={words}
          roles={roles}
          onClose={() => setSheet(null)}
          onPick={(toRole) => {
            const item = sheet.item;
            setSheet(null);
            void run(item, "escalate", toRole ? { to_role: toRole } : {});
          }}
        />
      ) : null}
    </div>
  );
}
