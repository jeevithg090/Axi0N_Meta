"use client";

// The owner's proof portal: pick a period, build a proof pack from the evidence chain, check it
// the way a recipient will, then share a signed, expiring link (with its QR code) or a PDF.

import { useCallback, useEffect, useState } from "react";

import { errorText } from "@/lib/api";

import { PackView } from "./_components/PackView";
import { VerifyPanel } from "./_components/VerifyPanel";
import {
  createPack,
  documentUrl,
  getPack,
  listDays,
  listPacks,
  revokePack,
  sharePack,
} from "./_lib/api";
import {
  AUDIENCES,
  AUDIENCE_NAMES,
  type Audience,
  type PackEntry,
  type ShareLink,
} from "./_lib/types";
import styles from "./inspector.module.css";

const POLL_MS = 2000;

function nextDay(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

export default function InspectorPage() {
  const [days, setDays] = useState<string[]>([]);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [audiences, setAudiences] = useState<Audience[]>([...AUDIENCES]);
  const [crewLang, setCrewLang] = useState("en");
  const [models, setModels] = useState(true);
  const [packs, setPacks] = useState<PackEntry[]>([]);
  const [open, setOpen] = useState<PackEntry | null>(null);
  const [link, setLink] = useState<ShareLink | null>(null);
  const [hours, setHours] = useState(72);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listPacks()
      .then(setPacks)
      .catch((e: unknown) => setError(errorText(e, "Could not list packs.")));
  }, []);

  useEffect(() => {
    listDays()
      .then((d) => {
        setDays(d);
        const last = d[d.length - 1];
        if (last) {
          setStart(last);
          setEnd(last);
        }
      })
      .catch(() => setDays([]));
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!open || open.status !== "building") return;
    const timer = window.setTimeout(() => {
      getPack(open.pack_id)
        .then((entry) => {
          setOpen(entry);
          if (entry.status !== "building") refresh();
        })
        .catch((e: unknown) => setError(errorText(e, "Could not read the pack.")));
    }, POLL_MS);
    return () => window.clearTimeout(timer);
  }, [open, refresh]);

  async function build() {
    setBusy(true);
    setError(null);
    setLink(null);
    try {
      const entry = await createPack({
        start: `${start}T00:00:00Z`,
        end: `${nextDay(end)}T00:00:00Z`,
        audiences,
        crew_lang: crewLang,
        models,
      });
      setOpen(entry);
      refresh();
    } catch (e) {
      setError(errorText(e, "Could not start the pack."));
    } finally {
      setBusy(false);
    }
  }

  async function openPack(id: string) {
    setLink(null);
    setError(null);
    try {
      setOpen(await getPack(id));
    } catch (e) {
      setError(errorText(e, "Could not read the pack."));
    }
  }

  async function share() {
    if (!open) return;
    try {
      setLink(await sharePack(open.pack_id, hours));
    } catch (e) {
      setError(errorText(e, "Could not make a link."));
    }
  }

  async function revoke() {
    if (!open) return;
    try {
      await revokePack(open.pack_id);
      setLink(null);
      setError("Every link to this pack now stops working.");
    } catch (e) {
      setError(errorText(e, "Could not revoke."));
    }
  }

  const toggle = (a: Audience) =>
    setAudiences((now) => (now.includes(a) ? now.filter((x) => x !== a) : [...now, a]));

  return (
    <div className={styles.page}>
      <h1>Proof portal</h1>
      <p className={styles.lede}>
        Make a proof pack for an inspector, an insurer, head office or the crew&apos;s
        representative. Every number comes from the site&apos;s hash-chained log, and the pack
        carries the records it was counted from with Merkle proofs, so the person you send it to can
        check it in their own browser. It is evidence for the period, not a certificate of
        compliance.
      </p>

      <section className={styles.card}>
        <h2>New pack</h2>
        <div className={styles.row}>
          <label className={styles.field}>
            From (UTC day)
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label className={styles.field}>
            To (UTC day, inclusive)
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
          <label className={styles.field}>
            Crew language
            <input
              value={crewLang}
              onChange={(e) => setCrewLang(e.target.value)}
              size={6}
              aria-label="Crew language code"
            />
          </label>
          <label className={styles.field}>
            <span>
              <input
                type="checkbox"
                checked={models}
                onChange={(e) => setModels(e.target.checked)}
              />{" "}
              Muse Spark summaries and clauses
            </span>
          </label>
        </div>
        <div className={styles.tabs} style={{ marginTop: 12 }}>
          {AUDIENCES.map((a) => (
            <button
              key={a}
              type="button"
              className={audiences.includes(a) ? styles.tabOn : styles.tab}
              aria-pressed={audiences.includes(a)}
              onClick={() => toggle(a)}
            >
              {AUDIENCE_NAMES[a]}
            </button>
          ))}
        </div>
        <button
          type="button"
          className={styles.button}
          onClick={() => void build()}
          disabled={busy || !start || !end || audiences.length === 0}
        >
          Create proof pack
        </button>
        {days.length > 0 ? (
          <p className={styles.muted}>Chain days on this site: {days.join(", ")}</p>
        ) : null}
        {error ? <p className={styles.bad}>{error}</p> : null}
      </section>

      {packs.length > 0 ? (
        <section className={styles.card}>
          <h2>Packs</h2>
          <table className={styles.table}>
            <tbody>
              {packs.map((p) => (
                <tr key={p.pack_id}>
                  <td>
                    <button
                      type="button"
                      className={styles.tab}
                      onClick={() => void openPack(p.pack_id)}
                    >
                      {p.title}
                    </button>
                  </td>
                  <td>{p.status}</td>
                  <td className={styles.mono}>{p.pack_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {open ? (
        <section>
          {open.status === "building" ? (
            <p className={styles.banner}>
              Building {open.pack_id}: counting from the chain, then Muse Spark finds each
              rule&apos;s clause and writes the summaries (up to a few minutes)…
            </p>
          ) : null}
          {open.status === "failed" ? (
            <p className={styles.bannerBad}>The pack failed: {open.error}</p>
          ) : null}
          {open.status === "ready" && open.pack && open.pack_hash ? (
            <>
              <h2>{open.pack.title}</h2>
              <div className={styles.card}>
                <div className={styles.row}>
                  <label className={styles.field}>
                    Link valid for (hours)
                    <input
                      type="number"
                      min={1}
                      max={2160}
                      value={hours}
                      onChange={(e) => setHours(Number(e.target.value))}
                    />
                  </label>
                  <button type="button" className={styles.button} onClick={() => void share()}>
                    Make share link and QR
                  </button>
                  <a className={styles.ghost} href={documentUrl(open.pack_id, "pdf")}>
                    Download PDF
                  </a>
                  <button type="button" className={styles.ghost} onClick={() => void revoke()}>
                    Revoke links
                  </button>
                </div>
                {link ? (
                  <div className={styles.share} style={{ marginTop: 12 }}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={link.qr} alt="QR code of the share link" />
                    <div>
                      <a href={link.url} target="_blank" rel="noreferrer" className={styles.mono}>
                        {link.url}
                      </a>
                      <p className={styles.muted}>
                        Expires {link.expires_at.replace("T", " ").slice(0, 16)} UTC. Anyone with
                        the link can read this pack; nothing else on the site.
                      </p>
                    </div>
                  </div>
                ) : null}
              </div>
              <VerifyPanel
                shared={{ pack: open.pack, pack_hash: open.pack_hash, expires_at: "" }}
                token={null}
              />
              <PackView pack={open.pack} />
            </>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
