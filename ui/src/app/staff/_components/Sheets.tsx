"use client";

import { useRef, useState } from "react";

import { roleWords, toRoleId, type Words } from "../_lib/i18n";
import type { RoleInfo, StaffItem } from "../_lib/types";
import styles from "../staff.module.css";

export function DoneSheet({
  item,
  words,
  onDone,
  onClose,
}: {
  item: StaffItem;
  words: Words;
  onDone: (photo: File | null, note: string) => void;
  onClose: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [note, setNote] = useState("");
  return (
    <div
      className={styles.sheetBack}
      role="dialog"
      aria-modal="true"
      aria-label={words.doneTitle}
      onClick={onClose}
    >
      <div className={styles.sheet} onClick={(e) => e.stopPropagation()}>
        <p className={styles.sheetTitle}>
          {words.doneTitle}: {item.title}
        </p>
        <input
          className={styles.input}
          value={note}
          maxLength={500}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Note (optional)"
          aria-label="Note"
        />
        <input
          ref={input}
          className={styles.hidden}
          type="file"
          accept="image/*"
          capture="environment"
          onChange={(e) => {
            const file = e.target.files?.[0] ?? null;
            if (file) onDone(file, note);
          }}
        />
        <button
          type="button"
          className={`${styles.primary} ${styles.primaryDone}`}
          onClick={() => input.current?.click()}
        >
          📷 {words.addPhoto}
        </button>
        <button type="button" className={styles.secondary} onClick={() => onDone(null, note)}>
          {words.skipPhoto}
        </button>
        <button type="button" className={styles.iconButton} onClick={onClose}>
          {words.cancel}
        </button>
      </div>
    </div>
  );
}

export function EscalateSheet({
  item,
  words,
  roles,
  onPick,
  onClose,
}: {
  item: StaffItem;
  words: Words;
  roles: RoleInfo[];
  onPick: (toRole: string | null) => void;
  onClose: () => void;
}) {
  const [custom, setCustom] = useState("");
  const others = roles.filter((r) => !item.roles.includes(r.role));
  const typed = toRoleId(custom);
  return (
    <div
      className={styles.sheetBack}
      role="dialog"
      aria-modal="true"
      aria-label={words.escalateTo}
      onClick={onClose}
    >
      <div className={styles.sheet} onClick={(e) => e.stopPropagation()}>
        <p className={styles.sheetTitle}>{words.escalateTo}</p>
        {item.next_role ? (
          <button type="button" className={styles.primary} onClick={() => onPick(null)}>
            {words.actions.escalate} → {roleWords(item.next_role)}
          </button>
        ) : null}
        {others.map((r) => (
          <button
            key={r.role}
            type="button"
            className={styles.choice}
            onClick={() => onPick(r.role)}
          >
            {roleWords(r.role)}
            <span className={styles.choiceSub}>{r.sources.join(", ")}</span>
          </button>
        ))}
        <div className={styles.askForm}>
          <input
            className={styles.input}
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            placeholder={words.otherRole}
            aria-label={words.otherRole}
          />
          <button
            type="button"
            className={styles.secondary}
            disabled={!typed}
            onClick={() => typed && onPick(typed)}
          >
            {words.send}
          </button>
        </div>
        <button type="button" className={styles.iconButton} onClick={onClose}>
          {words.cancel}
        </button>
      </div>
    </div>
  );
}
