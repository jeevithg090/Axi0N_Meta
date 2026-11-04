"use client";

// The worker rules page (P2-11), opened from the poster's or a rule card's QR code:
// `/worker/<plan_id>/<camera>?lang=`. Mobile first, large tap targets, no identity anywhere.

import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import { EmptyState, Skeleton } from "@/components/kit";
import { api } from "@/lib/api";
import { bucketLabel, STRINGS, uiLang, workerView, type WorkerPage } from "@/lib/worker";

import { DisputeSheet } from "./DisputeSheet";
import { VoiceReport } from "./VoiceReport";
import styles from "./worker.module.css";

export default function WorkerRulesPage() {
  return (
    <Suspense fallback={null}>
      <WorkerRules />
    </Suspense>
  );
}

function WorkerRules() {
  const { site, camera } = useParams<{ site: string; camera: string }>();
  const [lang, setLang] = useState<string | null>(useSearchParams().get("lang"));
  const [page, setPage] = useState<WorkerPage | null>(null);
  const [failed, setFailed] = useState(false);
  const [reload, setReload] = useState(0);
  const [disputing, setDisputing] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .workerPage(site, camera, lang)
      .then((body) => {
        if (!live) return;
        setPage(body);
        setFailed(false);
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [site, camera, lang, reload]);

  const closeSheet = useCallback(() => setDisputing(null), []);
  const refresh = useCallback(() => setReload((n) => n + 1), []);

  const strings = STRINGS[uiLang(page?.lang ?? lang ?? "en")];
  if (!page) {
    return (
      <div className="worker">
        {failed ? (
          <EmptyState title={strings.loadFailed} tone="error" icon="qr" />
        ) : (
          <Skeleton lines={5} label={strings.loading} />
        )}
      </div>
    );
  }
  const view = workerView(page);

  return (
    <section className={`worker ${styles.page}`} lang={page.lang} aria-labelledby="worker-heading">
      <header className="worker-header">
        <div>
          <h1 id="worker-heading">{strings.heading}</h1>
          <p className="muted">
            {page.site_name} · {page.camera_id}
          </p>
        </div>
        {page.languages.length > 1 && (
          <div
            role="group"
            aria-label={strings.language}
            className={`lang-switch ${styles.langSwitch}`}
          >
            {page.languages.map((code) => (
              <button
                key={code}
                type="button"
                className={`tap ${styles.tap}`}
                aria-pressed={code === page.lang}
                onClick={() => setLang(code)}
              >
                {code.toUpperCase()}
              </button>
            ))}
          </div>
        )}
      </header>

      <h2>{strings.watches}</h2>
      <ul className="worker-rules">
        {view.rules.map((rule) => (
          <li key={rule.ruleId} className="worker-rule">
            <p>{rule.text ?? <span className="muted">{strings.notTranslated}</span>}</p>
            <dl className="worker-counts">
              <div>
                <dt>{strings.counts.alerts}</dt>
                <dd>{rule.counts.alerts}</dd>
              </div>
              <div>
                <dt>{strings.counts.disputes}</dt>
                <dd>{rule.counts.disputes}</dd>
              </div>
              <div>
                <dt>{strings.counts.upheld}</dt>
                <dd>{rule.counts.upheld}</dd>
              </div>
            </dl>
          </li>
        ))}
      </ul>

      <h2>{strings.never}</h2>
      <ul className="never-list">
        {page.never_list.map((item) => (
          <li key={item}>{strings.neverItems[item]}</li>
        ))}
      </ul>
      <p>
        {strings.retention(page.retention_days)} {strings.privacy[page.privacy_mode]}
      </p>

      <h2>{strings.events}</h2>
      {view.events.length === 0 && <p className="muted">{strings.noEvents}</p>}
      <ul className="worker-events">
        {view.events.map((event) => (
          <li key={event.code} className={`worker-event ${styles.event}`}>
            <div>
              <strong className="code">{event.code}</strong>{" "}
              <span className="muted">{bucketLabel(event.bucketStart, page.lang)}</span>
              {event.ruleText && <p>{event.ruleText}</p>}
              {event.dispute && <span className="pill">{strings.disputed[event.dispute]}</span>}
            </div>
            <button
              type="button"
              className={`tap ${styles.tap}`}
              aria-label={`${strings.dispute} ${event.code}`}
              onClick={() => setDisputing(event.code)}
            >
              {strings.dispute}
            </button>
          </li>
        ))}
      </ul>

      <VoiceReport lang={page.lang} />

      {disputing && (
        <DisputeSheet
          code={disputing}
          strings={strings}
          onClose={closeSheet}
          onDisputed={refresh}
        />
      )}
    </section>
  );
}
