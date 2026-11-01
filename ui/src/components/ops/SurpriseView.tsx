"use client";

import { curvePoints, type SurpriseEvent } from "@/lib/ops";

const W = 320;
const H = 120;
const Z_MAX = 6;
const Z_ALERT = 3;

/** Surprise Lens: the z-score curve with its alert line, the SAM labels and the peak location. */
export function SurpriseView({
  event,
  heatmapUrl,
  onSuggest,
  busy,
}: {
  event: SurpriseEvent;
  heatmapUrl: string | null;
  onSuggest: () => void;
  busy: boolean;
}) {
  const peak = Math.max(...event.windows.map((w) => w.z));
  const alertY = H - (Z_ALERT / Z_MAX) * H;
  return (
    <article className="surprise" data-testid="surprise-view">
      <header>
        <h3>
          Surprise on {event.camera_id} <span className="muted">· peak z {peak.toFixed(1)}</span>
        </h3>
      </header>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="surprise-curve"
        role="img"
        aria-label={`Surprise curve, peak z ${peak.toFixed(1)}`}
      >
        <line x1="0" x2={W} y1={alertY} y2={alertY} className="surprise-threshold" />
        <polyline
          points={curvePoints(event.windows, W, H, Z_MAX)}
          className="surprise-line"
          fill="none"
        />
      </svg>
      <p className="muted">
        Dashed line: z = {Z_ALERT}, the alert level calibrated on normal activity.
      </p>
      {heatmapUrl ? (
        // The heatmap is a rendered still (skeleton/privacy-safe), served by the API.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={heatmapUrl}
          alt={`Heatmap: ${event.labels.join(", ")}`}
          className="surprise-heatmap"
        />
      ) : (
        <p className="muted">
          Hot spot at ({event.peak_xy[0]}, {event.peak_xy[1]}) px.
        </p>
      )}
      <p>
        What SAM sees there:{" "}
        {event.labels.map((label) => (
          <span key={label} className="hud-chip">
            {label}
          </span>
        ))}
      </p>
      <p className="muted">Surprise is notify-only: it never cuts power.</p>
      <button type="button" className="primary" onClick={onSuggest} disabled={busy}>
        Draft a rule from this
      </button>
    </article>
  );
}
