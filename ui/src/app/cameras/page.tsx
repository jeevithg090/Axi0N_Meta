"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { OwnerOnly } from "@/components/OwnerOnly";
import { PairPhoneButton } from "@/components/PairPhoneButton";
import { api, errorText, type CameraInfo } from "@/lib/api";
import { useAuth, useOwnerControls } from "@/lib/auth";
import {
  cameras,
  fillTemplate,
  healthLine,
  imageUrl,
  rankFits,
  snapshotUrl,
  surveyStep,
  type BrandTemplate,
  type CameraSurvey,
  type ConnectedCamera,
  type FoundDevice,
  type OnvifDevice,
  type StreamState,
} from "@/lib/cameras";
import { useDemo } from "@/lib/stream";

import styles from "./cameras.module.css";

type Path = "url" | "scan" | "phone" | "clip";
const PATHS: { id: Path; label: string }[] = [
  { id: "url", label: "Paste a URL" },
  { id: "scan", label: "Scan the network" },
  { id: "phone", label: "Pair a phone" },
  { id: "clip", label: "Upload a clip" },
];
const POLL_MS = 2000;

function stateClass(state: StreamState): string {
  if (state === "live") return styles.live ?? "";
  if (state === "connecting" || state === "reconnecting") return styles.waiting ?? "";
  return styles.down ?? "";
}

/** Paste any URL (RTSP, HLS, MJPEG, YouTube live...) with optional camera credentials. */
function UrlForm({ onAdded, preset }: { onAdded: () => void; preset: string }) {
  const [url, setUrl] = useState(preset);
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [brands, setBrands] = useState<BrandTemplate[]>([]);
  const [host, setHost] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setUrl(preset), [preset]);
  useEffect(() => {
    cameras
      .brands()
      .then(setBrands)
      .catch(() => setBrands([]));
  }, []);

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await cameras.add({ url: url.trim(), name: name.trim(), username, password });
      setPassword("");
      onAdded();
    } catch (err) {
      setError(errorText(err, "Could not add the camera."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className={styles.form} onSubmit={(e) => void submit(e)} aria-label="Add by URL">
      <input
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="rtsp://192.168.1.64:554/Streaming/Channels/101 · https://…/index.m3u8 · YouTube live link"
        aria-label="Camera URL"
        required
      />
      <div className={styles.row}>
        <select
          aria-label="Camera maker"
          defaultValue=""
          onChange={(e) => {
            const brand = brands.find((b) => b.brand === e.target.value);
            if (brand) setUrl(fillTemplate(brand.main, host));
          }}
        >
          <option value="">Camera maker (fills the URL)…</option>
          {brands.map((b) => (
            <option key={b.brand} value={b.brand}>
              {b.brand}
            </option>
          ))}
        </select>
        <input
          value={host}
          onChange={(e) => setHost(e.target.value)}
          placeholder="Camera IP (for the maker's URL)"
          aria-label="Camera IP"
        />
      </div>
      <div className={styles.row}>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name (optional; Muse Spark names it)"
          aria-label="Camera name"
          maxLength={60}
        />
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="User (optional)"
          aria-label="Camera user"
          autoComplete="off"
        />
        <input
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password (optional)"
          aria-label="Camera password"
          type="password"
          autoComplete="new-password"
        />
      </div>
      <p className={styles.small + " muted"}>
        The user and password are kept only on the Rulecam server and never shown or logged.
      </p>
      <button type="submit" className="primary" disabled={busy}>
        {busy ? "Connecting…" : "Connect"}
      </button>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </form>
  );
}

/** One device the scan found: ONVIF devices list their streams once the owner signs in. */
function DeviceRow({
  device,
  onUse,
  onAdded,
}: {
  device: FoundDevice;
  onUse: (url: string) => void;
  onAdded: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [onvif, setOnvif] = useState<OnvifDevice | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const streams = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      setOnvif(await cameras.onvif(device.xaddr ?? device.address, username, password));
    } catch (err) {
      setError(errorText(err, "The camera did not answer."));
    } finally {
      setBusy(false);
    }
  };

  const connect = async (url: string): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      await cameras.add({ url, username, password }); // Muse Spark names it from what it sees
      onAdded();
    } catch (err) {
      setError(errorText(err, "Could not add the camera."));
    } finally {
      setBusy(false);
    }
  };

  const label = [device.name, device.hardware, device.location].filter(Boolean).join(" · ");
  return (
    <div className={styles.device} data-testid="found-device">
      <strong>
        {device.address} {label && `— ${label}`}
      </strong>
      <span className={styles.small}>
        {device.via === "onvif" ? "ONVIF" : "RTSP port open"}
        {device.rtsp_ports.length > 0 && ` · ports ${device.rtsp_ports.join(", ")}`}
      </span>
      {device.via === "onvif" ? (
        <div className={styles.row}>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="User"
            aria-label="ONVIF user"
            autoComplete="off"
          />
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            type="password"
            aria-label="ONVIF password"
            autoComplete="new-password"
          />
          <button type="button" onClick={() => void streams()} disabled={busy}>
            List streams
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => onUse(`rtsp://${device.address}:${device.rtsp_ports[0] ?? 554}/`)}
        >
          Use this address
        </button>
      )}
      {onvif && (
        <div className={styles.devices}>
          <span className={styles.small}>
            {[onvif.manufacturer, onvif.model].filter(Boolean).join(" ")}
          </span>
          {onvif.profiles.map((p) => (
            <div key={p.token} className={styles.row}>
              <span className={styles.mono}>
                {p.name} · {p.encoding ?? "?"} {p.width && `${p.width}x${p.height}`}
              </span>
              {p.rtsp_url ? (
                <button
                  type="button"
                  onClick={() => void connect(p.rtsp_url ?? "")}
                  disabled={busy}
                >
                  Connect
                </button>
              ) : (
                <span className={styles.error}>{p.error}</span>
              )}
            </div>
          ))}
        </div>
      )}
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </div>
  );
}

function ScanPanel({ onUse, onAdded }: { onUse: (url: string) => void; onAdded: () => void }) {
  const [devices, setDevices] = useState<FoundDevice[] | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scan = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const result = await cameras.scan();
      setDevices(result.devices);
      setNotes([
        `Scanned ${result.subnet ?? "the local network"} in ${result.took_s} s.`,
        ...result.notes,
      ]);
    } catch (err) {
      setError(errorText(err, "The scan failed."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.form}>
      <p className="muted">
        Finds ONVIF cameras and NVRs (WS-Discovery) and anything with an RTSP port open on this
        network. It only finds cameras when Rulecam runs on the site&apos;s own network.
      </p>
      <button type="button" className="primary" onClick={() => void scan()} disabled={busy}>
        {busy ? "Scanning…" : "Scan the network"}
      </button>
      {notes.map((n) => (
        <p key={n} className={styles.small}>
          {n}
        </p>
      ))}
      {devices?.length === 0 && <p className="muted">No cameras answered.</p>}
      <div className={styles.devices}>
        {devices?.map((d) => (
          <DeviceRow key={d.address} device={d} onUse={onUse} onAdded={onAdded} />
        ))}
      </div>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </div>
  );
}

function ClipForm({ onAdded }: { onAdded: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await cameras.upload(file, name.trim());
      onAdded();
    } catch (err) {
      setError(errorText(err, "Could not upload the clip."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className={styles.form} onSubmit={(e) => void submit(e)} aria-label="Upload a clip">
      <p className="muted">The clip loops forever as a virtual camera (labelled as an upload).</p>
      <input
        type="file"
        accept="video/*"
        aria-label="Video clip"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        required
      />
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Name (optional)"
        aria-label="Clip camera name"
        maxLength={60}
      />
      <button type="submit" className="primary" disabled={busy || !file}>
        {busy ? "Uploading…" : "Upload and connect"}
      </button>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </form>
  );
}

/** Spark's description, SAM 3.1's chips and the use-case fit. */
function SurveyView({ survey }: { survey: CameraSurvey }) {
  const running = !["done", "failed", "blocked", "unavailable"].includes(survey.status);
  return (
    <div data-testid="survey">
      <p className={styles.small}>
        <strong>{surveyStep(survey.status)}</strong>
        {survey.timings.total_s !== undefined && ` · ${survey.timings.total_s.toFixed(1)} s`}
        {running && " ⏳"}
      </p>
      {survey.look && (
        <p className={styles.small}>
          {survey.look.description}
          {survey.look.image_problems && (
            <span className="muted"> Image: {survey.look.image_problems}</span>
          )}
        </p>
      )}
      {survey.detections.length > 0 && (
        <div className={styles.chips} aria-label="What SAM 3.1 detects">
          {survey.detections.map((d) => (
            <span
              key={d.concept}
              className={d.count > 0 ? styles.chip : styles.chipNone}
              title={d.error ?? `mean confidence ${d.mean_confidence ?? "-"}`}
            >
              {d.concept} ×{d.count}
              {d.max_confidence !== null && ` · ${Math.round(d.max_confidence * 100)}%`}
            </span>
          ))}
        </div>
      )}
      {survey.fit && (
        <>
          <div className={styles.chips} aria-label="Use cases">
            {rankFits(survey.fit.use_cases).map((u) => (
              <span key={u.use_case} className={styles[u.fit]} title={u.why}>
                {u.use_case}: {u.fit}
              </span>
            ))}
          </div>
          {rankFits(survey.fit.use_cases)
            .filter((u) => u.fit !== "poor")
            .map((u) => (
              <div key={u.use_case} className={styles.small}>
                <p className={styles.small}>{u.why}</p>
                <ul>
                  {u.first_rules.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              </div>
            ))}
          {survey.fit.placement_tip && (
            <p className={styles.small}>
              <strong>Tip:</strong> {survey.fit.placement_tip}
            </p>
          )}
        </>
      )}
      {survey.marked && (
        <a href={imageUrl(survey.marked)} target="_blank" rel="noreferrer" className={styles.small}>
          See SAM 3.1&apos;s boxes
        </a>
      )}
      {survey.error && <p className={styles.error}>{survey.error}</p>}
    </div>
  );
}

function CameraTile({
  camera,
  tick,
  onChange,
}: {
  camera: ConnectedCamera;
  tick: number;
  onChange: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const act = async (run: () => Promise<unknown>): Promise<void> => {
    setError(null);
    try {
      await run();
      onChange();
    } catch (err) {
      setError(errorText(err, "That did not work."));
    }
  };
  const h = camera.health;
  return (
    <article className={styles.tile} data-testid="camera-tile">
      <div className={styles.title}>
        <h3>{camera.name}</h3>
        <span className={stateClass(h.state)}>{camera.blocked ? "blocked" : h.state}</span>
      </div>
      {h.chunks > 0 ? (
        // eslint-disable-next-line @next/next/no-img-element -- live owner-only snapshot
        <img className={styles.shot} src={snapshotUrl(camera.camera_id, tick)} alt={camera.name} />
      ) : (
        <div className={styles.shot} aria-label="No frame yet" />
      )}
      <span className={styles.mono}>
        {camera.camera_id} · {camera.kind} · {camera.display_url}
        {camera.provenance !== "recorded" && ` · ${camera.provenance}`}
      </span>
      <span className={styles.small}>{healthLine(h)}</span>
      {h.title && <span className={styles.small}>{h.title}</span>}
      {h.last_error && h.state !== "live" && <p className={styles.error}>{h.last_error}</p>}
      {camera.blocked && <p className={styles.error}>{camera.blocked}</p>}
      {camera.survey && <SurveyView survey={camera.survey} />}
      <div className={styles.actions}>
        <button type="button" onClick={() => void act(() => cameras.survey(camera.camera_id))}>
          Look again
        </button>
        <button type="button" onClick={() => void act(() => cameras.restart(camera.camera_id))}>
          Reconnect
        </button>
        <button type="button" onClick={() => void act(() => cameras.remove(camera.camera_id))}>
          Remove
        </button>
      </div>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </article>
  );
}

/** Cameras that came another way (paired phones, `rulecam capture`): snapshot and first look. */
function OtherTile({ camera, tick }: { camera: CameraInfo; tick: number }) {
  const [survey, setSurvey] = useState<CameraSurvey | null>(null);
  useEffect(() => {
    cameras
      .getSurvey(camera.camera_id)
      .then(setSurvey)
      .catch(() => setSurvey(null));
  }, [camera.camera_id, tick]);
  return (
    <article className={styles.tile} data-testid="other-tile">
      <div className={styles.title}>
        <h3>{camera.label}</h3>
        <span className={styles.state}>{camera.source}</span>
      </div>
      {/* eslint-disable-next-line @next/next/no-img-element -- live owner-only snapshot */}
      <img className={styles.shot} src={snapshotUrl(camera.camera_id, tick)} alt={camera.label} />
      {survey && <SurveyView survey={survey} />}
      <button type="button" onClick={() => void cameras.survey(camera.camera_id).then(setSurvey)}>
        First look (Spark + SAM 3.1)
      </button>
    </article>
  );
}

function LiveView() {
  const [path, setPath] = useState<Path>("url");
  const [preset, setPreset] = useState("");
  const [list, setList] = useState<ConnectedCamera[]>([]);
  const [others, setOthers] = useState<CameraInfo[]>([]);
  const [tick, setTick] = useState(() => Date.now());
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const [mine, all] = await Promise.all([cameras.list(), api.cameras()]);
      setList(mine);
      const ids = new Set(mine.map((c) => c.camera_id));
      setOthers(all.filter((c) => !ids.has(c.camera_id)));
      setError(null);
    } catch (err) {
      setError(errorText(err, "Could not load cameras."));
    }
    setTick(Date.now());
  }, []);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const use = (url: string): void => {
    setPreset(url);
    setPath("url");
  };

  return (
    <>
      <div className={styles.tabs} role="tablist" aria-label="How to add a camera">
        {PATHS.map((p) => (
          <button
            key={p.id}
            type="button"
            role="tab"
            aria-selected={path === p.id}
            className={path === p.id ? styles.tabActive : styles.tab}
            onClick={() => setPath(p.id)}
          >
            {p.label}
          </button>
        ))}
      </div>
      <div className="site-panel">
        {path === "url" && <UrlForm onAdded={() => void refresh()} preset={preset} />}
        {path === "scan" && <ScanPanel onUse={use} onAdded={() => void refresh()} />}
        {path === "phone" && (
          <div className={styles.form}>
            <p className="muted">
              Scan the QR code with any phone; it streams like a camera and shows up below.
            </p>
            <PairPhoneButton />
          </div>
        )}
        {path === "clip" && <ClipForm onAdded={() => void refresh()} />}
      </div>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
      <div className={styles.grid}>
        {list.map((c) => (
          <CameraTile key={c.camera_id} camera={c} tick={tick} onChange={() => void refresh()} />
        ))}
        {others.map((c) => (
          <OtherTile key={c.camera_id} camera={c} tick={tick} />
        ))}
      </div>
      {list.length === 0 && others.length === 0 && (
        <p className="muted">No cameras yet. Add one above.</p>
      )}
    </>
  );
}

/** Connect any camera in 30 seconds: URL, network scan, phone or clip, then a first look. */
export default function CamerasPage() {
  const owner = useOwnerControls();
  const { phase } = useAuth();
  const demo = useDemo() === true;
  return (
    <section aria-label="Connect cameras" className="site-page">
      <h2>Connect cameras</h2>
      <p className="muted">
        Bring the cameras you already have: CCTV and NVRs over RTSP or ONVIF (Hikvision, Dahua,
        Reolink, Axis…), HLS or MJPEG streams, a public live stream, a phone or a clip. As soon as a
        camera is live, Muse Spark names it and says what it sees, SAM 3.1 checks what it can
        detect, and Spark says which use cases it fits.
      </p>
      {demo ? (
        <p className="muted" data-testid="cameras-demo">
          The recorded demo has no live cameras. Open Rulecam against its API to connect one.
        </p>
      ) : owner && phase !== "demo" ? (
        <LiveView />
      ) : (
        <OwnerOnly checking={phase === "checking"} what="Connecting cameras" />
      )}
    </section>
  );
}
