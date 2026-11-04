"use client";

import { useEffect, useState } from "react";

import { unlockAudio } from "../_lib/alerting";
import { staffApi } from "../_lib/client";
import { LANG_NAMES, WORDS, roleWords, toRoleId } from "../_lib/i18n";
import type { Lang, RoleInfo } from "../_lib/types";
import styles from "../staff.module.css";

const LANGS: Lang[] = ["en", "es", "hi"];

export interface Setup {
  lang: Lang;
  role: string;
  autoRead: boolean;
}

export function Onboarding({
  initial,
  onDone,
}: {
  initial: Partial<Setup>;
  onDone: (setup: Setup) => void;
}) {
  const [lang, setLang] = useState<Lang>(initial.lang ?? "en");
  const [role, setRole] = useState(initial.role ?? "");
  const [custom, setCustom] = useState("");
  const [roles, setRoles] = useState<RoleInfo[]>([]);
  const [autoRead, setAutoRead] = useState(initial.autoRead ?? true);
  const words = WORDS[lang];

  useEffect(() => {
    staffApi
      .roles()
      .then(setRoles)
      .catch(() => setRoles([]));
  }, []);

  const typed = toRoleId(custom);
  const chosen = typed ?? role;

  const start = async () => {
    if (!chosen) return;
    unlockAudio();
    try {
      if (typeof Notification !== "undefined" && Notification.permission === "default") {
        await Notification.requestPermission();
      }
    } catch {
      // the feed still buzzes and speaks while open
    }
    onDone({ lang, role: chosen, autoRead });
  };

  return (
    <div className={styles.onboard}>
      <div>
        <h1 className={styles.onboardTitle}>{words.appName}</h1>
        <p className={styles.onboardLead}>{words.roleHint}</p>
      </div>
      <div className={styles.choices} role="radiogroup" aria-label={words.chooseLanguage}>
        <p className={styles.sectionLabel}>{words.chooseLanguage}</p>
        {LANGS.map((code) => (
          <button
            key={code}
            type="button"
            role="radio"
            aria-checked={lang === code}
            className={`${styles.choice} ${lang === code ? styles.choiceOn : ""}`}
            onClick={() => setLang(code)}
          >
            {LANG_NAMES[code]}
            <span className={styles.choiceSub}>{code.toUpperCase()}</span>
          </button>
        ))}
      </div>
      <div className={styles.choices} role="radiogroup" aria-label={words.chooseRole}>
        <p className={styles.sectionLabel}>{words.chooseRole}</p>
        {roles.map((r) => (
          <button
            key={r.role}
            type="button"
            role="radio"
            aria-checked={!typed && role === r.role}
            className={`${styles.choice} ${!typed && role === r.role ? styles.choiceOn : ""}`}
            onClick={() => {
              setRole(r.role);
              setCustom("");
            }}
          >
            {roleWords(r.role)}
            <span className={styles.choiceSub}>{r.sources.join(", ")}</span>
          </button>
        ))}
        <input
          className={styles.input}
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          placeholder={`${words.otherRole} (steward, stock, dock_lead…)`}
          aria-label={words.otherRole}
        />
      </div>
      <label className={styles.toggle}>
        <input type="checkbox" checked={autoRead} onChange={(e) => setAutoRead(e.target.checked)} />
        {words.autoRead}
      </label>
      <button
        type="button"
        className={styles.primary}
        disabled={!chosen}
        onClick={() => void start()}
      >
        {chosen ? `${words.start} · ${roleWords(chosen)}` : words.noRole}
      </button>
    </div>
  );
}
