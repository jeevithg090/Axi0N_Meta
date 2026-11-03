"use client";

import { useEffect, useState, type FormEvent } from "react";

import { OwnerOnly } from "@/components/OwnerOnly";
import { errorText } from "@/lib/api";
import { useAuth, useOwnerControls } from "@/lib/auth";
import {
  markedUrl,
  site,
  sourceNote,
  worstLevel,
  type CrowdReport,
  type ShelfReport,
  type SiteContext,
  type Vertical,
} from "@/lib/site";
import { useDemo } from "@/lib/stream";

/** Live site context: what is happening around the site, every source checked. */
function ContextPanel() {
  const [vertical, setVertical] = useState<Vertical>("festival");
  const [notes, setNotes] = useState("");
  const [context, setContext] = useState<SiteContext | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setContext(null);
    site
      .latestContext(vertical)
      .then((found) => live && setContext(found))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [vertical]);

  const refresh = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setContext(await site.refreshContext(vertical, notes.trim()));
    } catch (err) {
      setError(errorText(err, "Could not reach the Rulecam API."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label="Site context" className="site-panel">
      <h3>Around the site today</h3>
      <p className="muted">
        Muse Spark searches the web for weather warnings, running orders, holidays, local events and
        food recalls. An item is shown only if its source is a page the search actually returned.
      </p>
      <form onSubmit={(e) => void refresh(e)} className="composer-row">
        <label>
          Site{" "}
          <select
            value={vertical}
            onChange={(e) => setVertical(e.target.value as Vertical)}
            aria-label="Kind of site"
          >
            <option value="festival">Festival</option>
            <option value="supermarket">Supermarket</option>
            <option value="general">Other</option>
          </select>
        </label>
        <input
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Event name or street (optional)"
          maxLength={500}
          aria-label="Notes"
        />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Searching…" : "Refresh"}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {context && (
        <>
          <p className="muted">
            {context.searches} searches, {context.pages_seen} pages read at{" "}
            {new Date(context.asked_at).toLocaleString()}.
          </p>
          <ul className="site-items">
            {context.items.map((item) => (
              <li key={`${item.title}-${item.source_url}`}>
                <strong>
                  [{item.kind}] {item.title}
                </strong>
                {item.when && <span className="muted"> · {item.when}</span>}
                <p>{item.summary}</p>
                {item.watch && <p>Watch: {item.watch}</p>}
                <p className="muted">
                  <a href={item.source_url} target="_blank" rel="noreferrer noopener">
                    {item.source_title || item.source_url}
                  </a>{" "}
                  ({sourceNote(item)})
                </p>
              </li>
            ))}
          </ul>
          {context.dropped.length > 0 && (
            <details>
              <summary>Left out ({context.dropped.length})</summary>
              <ul>
                {context.dropped.map((d) => (
                  <li key={`${d.title}-${d.source_url}`}>
                    {d.title}: {d.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}

/** Supermarket shelf gaps on a photo: Spark finds them, SAM 3.1 confirms nothing is there. */
function ShelfPanel() {
  const [report, setReport] = useState<ShelfReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scan = async (file: File | undefined): Promise<void> => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      setReport(await site.scanShelfImage(file));
    } catch (err) {
      setError(errorText(err, "Could not reach the Rulecam API."));
    } finally {
      setBusy(false);
    }
  };

  const image = report ? markedUrl(report) : null;
  return (
    <section aria-label="Shelf gaps" className="site-panel">
      <h3>Shelf gaps</h3>
      <p className="muted">
        A photo of a bay. Muse Spark marks each empty facing; SAM 3.1 checks that no product is in
        it.
      </p>
      <input
        type="file"
        accept="image/jpeg,image/png"
        aria-label="Shelf photo"
        disabled={busy}
        onChange={(e) => void scan(e.target.files?.[0])}
      />
      {busy && <p role="status">Scanning…</p>}
      {error && <p role="alert">{error}</p>}
      {report && (
        <>
          <p>
            {report.gaps.length} gap{report.gaps.length === 1 ? "" : "s"} confirmed,{" "}
            {report.rejected.length} rejected, {report.products_seen} products seen.
          </p>
          {image && (
            // eslint-disable-next-line @next/next/no-img-element -- an API image, not static
            <img src={image} alt="Shelf with the confirmed gaps numbered" className="site-image" />
          )}
          <ol>
            {report.gaps.map((g) => (
              <li key={g.number}>
                {g.section}: about {g.facings} facings. Next to it: {g.neighbours}
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

const minutesAgo = (n: number): string => new Date(Date.now() - n * 60_000).toISOString();

/** Festival crowd density episodes on one camera, each checked on video. */
function CrowdPanel() {
  const [camera, setCamera] = useState("");
  const [minutes, setMinutes] = useState(15);
  const [report, setReport] = useState<CrowdReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scan = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    if (!camera.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setReport(await site.scanCrowd(camera.trim(), minutesAgo(minutes), minutesAgo(0)));
    } catch (err) {
      setError(errorText(err, "Could not reach the Rulecam API."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label="Crowd density" className="site-panel">
      <h3>Crowd density</h3>
      <p className="muted">
        People per square metre from the SAM 3.1 tracks; each crowded spell is watched by Muse Spark
        for surges, people down and blocked exits.
      </p>
      <form onSubmit={(e) => void scan(e)} className="composer-row">
        <input
          value={camera}
          onChange={(e) => setCamera(e.target.value)}
          placeholder="Camera id"
          aria-label="Camera id"
        />
        <label>
          Last{" "}
          <input
            type="number"
            min={1}
            max={360}
            value={minutes}
            onChange={(e) => setMinutes(Number(e.target.value) || 15)}
            aria-label="Minutes"
          />{" "}
          min
        </label>
        <button type="submit" className="primary" disabled={busy || !camera.trim()}>
          {busy ? "Scanning…" : "Scan"}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {report && (
        <>
          <p>
            {report.keyframes} keyframes, {report.episodes.length} crowded spell
            {report.episodes.length === 1 ? "" : "s"}, worst level: {worstLevel(report)}.
          </p>
          <ul>
            {report.episodes.map((ep) => (
              <li key={ep.episode.start}>
                <strong>{ep.escalate ? "Escalate" : ep.disputed ? "Check by eye" : "Watch"}</strong>{" "}
                {new Date(ep.episode.start).toLocaleTimeString()}–
                {new Date(ep.episode.end).toLocaleTimeString()}: peak{" "}
                {ep.episode.peak_density.toFixed(1)} per m² ({ep.episode.level})
                {ep.reasons.length > 0 && <span>. {ep.reasons.join("; ")}</span>}
                {ep.check && <p className="muted">{ep.check.summary}</p>}
                {ep.check_error && <p className="muted">Video check failed: {ep.check_error}</p>}
              </li>
            ))}
          </ul>
          {report.notes.map((note) => (
            <p key={note} className="muted">
              {note}
            </p>
          ))}
        </>
      )}
    </section>
  );
}

/** Site tools for the festival and supermarket use cases (owner-only, live only). */
export default function SitePage() {
  const owner = useOwnerControls();
  const { phase } = useAuth();
  const demo = useDemo() === true;
  if (demo) {
    return (
      <section aria-label="Site tools">
        <h2>Site tools</h2>
        <p className="muted">Site tools call the live models; the recorded demo has none.</p>
      </section>
    );
  }
  if (!owner) {
    return (
      <section aria-label="Site tools">
        <h2>Site tools</h2>
        <OwnerOnly checking={phase === "checking"} what="Site tools" />
      </section>
    );
  }
  return (
    <section aria-label="Site tools" className="site-page">
      <h2>Site tools</h2>
      <ContextPanel />
      <ShelfPanel />
      <CrowdPanel />
    </section>
  );
}
