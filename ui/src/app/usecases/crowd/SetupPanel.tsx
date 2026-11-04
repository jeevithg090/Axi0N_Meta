"use client";

import { useState } from "react";

import styles from "./crowd.module.css";
import type { CameraStatus, SetupProposal, Venue, WatchRequest } from "./crowdApi";
import { crowdApi, fileUrl } from "./crowdApi";

const EXAMPLE =
  "यह कैमरा गेट को ऊपर से देखता है। गेट के सामने भीड़ खतरनाक होने से पहले चेतावनी दें, स्टीवर्ड्स को बताएं क्या करना है, और हिंदी और अंग्रेज़ी में शांति से घोषणा करें।";

function draftWatch(proposal: SetupProposal, current: CameraStatus | undefined): WatchRequest {
  return {
    camera_id: proposal.camera_id,
    instruction: proposal.instruction,
    languages: proposal.draft.languages,
    role: proposal.role,
    escalate_to: proposal.escalate_to,
    ack_within_s: 60,
    zones: proposal.zones,
    concept: proposal.draft.concept,
    grid: Math.max(1, Math.min(4, proposal.draft.grid)),
    floor: current?.watched?.floor ?? null,
  };
}

/** Step 1: the operator's words → Muse Spark's proposed watch → the operator starts it. */
export function SetupPanel({
  cameras,
  venue,
  current,
}: {
  cameras: CameraStatus[];
  venue: Venue;
  current?: string;
}) {
  const [cameraId, setCameraId] = useState("");
  const [instruction, setInstruction] = useState(EXAMPLE);
  const [proposal, setProposal] = useState<SetupProposal | null>(null);
  const [watchJson, setWatchJson] = useState("");
  const [venueJson, setVenueJson] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const camera = cameraId || current || cameras[0]?.camera_id || "";

  const run = async (what: string, task: () => Promise<void>): Promise<void> => {
    setBusy(true);
    setMessage(`${what}…`);
    try {
      await task();
    } catch (exc) {
      setMessage(exc instanceof Error ? exc.message : `${what} failed`);
    } finally {
      setBusy(false);
    }
  };

  const ask = () =>
    run("Muse Spark is reading the instruction and the frame", async () => {
      const made = await crowdApi.setup(camera, instruction);
      setProposal(made);
      setWatchJson(
        JSON.stringify(
          draftWatch(
            made,
            cameras.find((c) => c.camera_id === camera),
          ),
          null,
          1,
        ),
      );
      setMessage(`Proposal in ${made.seconds} s. Check the zones, edit if needed, then start.`);
    });

  const start = () =>
    run("Starting the watch", async () => {
      const made = await crowdApi.watch(JSON.parse(watchJson) as WatchRequest);
      setMessage(`Watching ${made.camera_id} since ${new Date(made.since).toLocaleTimeString()}.`);
    });

  const saveVenue = () =>
    run("Saving the venue", async () => {
      const saved = await crowdApi.setVenue(JSON.parse(venueJson) as Venue);
      setMessage(`Venue saved: ${saved.places.length} places.`);
    });

  return (
    <div className={styles.card}>
      <h2>Set up a watch from plain words</h2>
      <div className={styles.row}>
        <label className={styles.small}>
          camera{" "}
          <select
            className={styles.input}
            value={camera}
            onChange={(e) => setCameraId(e.target.value)}
          >
            {cameras.map((c) => (
              <option key={c.camera_id} value={c.camera_id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <textarea
        className={styles.textarea}
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
        aria-label="Instruction in any language"
      />
      <div className={styles.row}>
        <button
          type="button"
          className={styles.button}
          disabled={busy || !camera}
          onClick={() => void ask()}
        >
          Ask Muse Spark to set it up
        </button>
        <button
          type="button"
          className={styles.button}
          onClick={() => setVenueJson(JSON.stringify(venue, null, 1))}
        >
          Edit venue layout
        </button>
      </div>
      {message && <span className={styles.small}>{message}</span>}
      {proposal && (
        <>
          <p style={{ margin: 0 }}>{proposal.draft.summary}</p>
          <span className={styles.small}>
            View: {proposal.draft.view} · count “{proposal.draft.concept}” on {proposal.draft.grid}×
            {proposal.draft.grid} tiles · languages {proposal.draft.languages.join(", ")} · alert{" "}
            {proposal.role} then {proposal.escalate_to.join(", ") || "nobody"} · calibration:{" "}
            {proposal.draft.calibration}
          </span>
          <div className={styles.frame}>
            {/* eslint-disable-next-line @next/next/no-img-element -- live API image */}
            <img src={fileUrl(proposal.frame)} alt="The frame Muse Spark read" />
            <svg
              className={styles.overlay}
              viewBox={`0 0 ${proposal.width} ${proposal.height}`}
              aria-hidden
            >
              {proposal.zones.map((z) => (
                <g key={z.name}>
                  <polygon
                    points={z.polygon_px.map(([x, y]) => `${x},${y}`).join(" ")}
                    fill="rgba(255,235,59,0.2)"
                    stroke="#ffeb3b"
                    strokeWidth={3}
                  />
                  <text
                    x={z.polygon_px[0]?.[0] ?? 0}
                    y={(z.polygon_px[0]?.[1] ?? 0) + 18}
                    fill="#ffeb3b"
                    stroke="#000"
                    strokeWidth={0.8}
                    fontSize={18}
                  >
                    {z.name}
                  </text>
                </g>
              ))}
            </svg>
          </div>
          <textarea
            className={styles.code}
            value={watchJson}
            onChange={(e) => setWatchJson(e.target.value)}
            aria-label="The watch to start (editable)"
          />
          <div className={styles.row}>
            <button
              type="button"
              className={styles.button}
              disabled={busy}
              onClick={() => void start()}
            >
              Start the watch
            </button>
            <span className={styles.small}>
              Overhead views need four floor points with their distances (`floor`).
            </span>
          </div>
        </>
      )}
      {venueJson && (
        <>
          <textarea
            className={styles.code}
            value={venueJson}
            onChange={(e) => setVenueJson(e.target.value)}
            aria-label="Venue layout (editable)"
          />
          <button
            type="button"
            className={styles.button}
            disabled={busy}
            onClick={() => void saveVenue()}
          >
            Save the venue layout
          </button>
        </>
      )}
    </div>
  );
}
