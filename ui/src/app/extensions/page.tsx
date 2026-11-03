"use client";

import { useEffect, useState, type FormEvent } from "react";

import { ExtensionCard } from "@/components/extensions/ExtensionCard";
import { OwnerOnly } from "@/components/OwnerOnly";
import { SAMPLE_EXTENSION } from "@/fixtures/extension-sample";
import { errorText } from "@/lib/api";
import { useAuth, useOwnerControls } from "@/lib/auth";
import { extensions, parseLine, type ExtensionRecord } from "@/lib/extensions";
import { useDemo } from "@/lib/stream";

const SAMPLE = SAMPLE_EXTENSION;

function Intro() {
  return (
    <p className="muted">
      When the rule language cannot say what you need (&ldquo;alert if a pallet is stacked above the
      yellow line&rdquo;), Muse Code writes a small check for it. Rulecam only lets it run in a
      sandbox, tests it with its own cases and Rulecam&apos;s boundary cases, attacks it with
      mutants, has a second model review it, and runs it only after you approve it.
    </p>
  );
}

/** The recorded sample: the reference check proved by the real sandbox; model replies are fakes. */
function SampleView() {
  return (
    <>
      <p role="note" data-testid="sample-banner" className="site-panel">
        <strong>SAMPLE DATA, not a live run.</strong> This is the reference check from the test
        suite. Its proof (sandbox, boundary cases, mutants) ran for real; the Muse Code and reviewer
        replies are test fakes, not live model output.
      </p>
      <ExtensionCard record={SAMPLE} />
    </>
  );
}

/** The owner's request form. */
function RequestForm({ onMade }: { onMade: (record: ExtensionRecord) => void }) {
  const [request, setRequest] = useState("");
  const [concept, setConcept] = useState("pallet");
  const [camera, setCamera] = useState("");
  const [lineName, setLineName] = useState("yellow");
  const [line, setLine] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    const points = line.trim() ? parseLine(line) : null;
    if (line.trim() && !points) {
      setError("A line is four numbers: x1,y1,x2,y2 in pixels.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onMade(
        await extensions.propose({
          request: request.trim(),
          concept: concept.trim(),
          camera_id: camera.trim(),
          lines: points ? { [lineName.trim() || "line"]: points } : {},
        }),
      );
    } catch (err) {
      setError(errorText(err, "Could not reach the Rulecam API."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={(e) => void submit(e)} className="site-panel" aria-label="New extension">
      <input
        value={request}
        onChange={(e) => setRequest(e.target.value)}
        placeholder="Alert if a pallet is stacked above the yellow line"
        aria-label="What should the camera check?"
        required
        minLength={3}
        maxLength={500}
        style={{ width: "100%" }}
      />
      <input
        value={concept}
        onChange={(e) => setConcept(e.target.value)}
        aria-label="Object"
        required
      />
      <input
        value={camera}
        onChange={(e) => setCamera(e.target.value)}
        placeholder="Camera id"
        aria-label="Camera"
        required
      />
      <input
        value={lineName}
        onChange={(e) => setLineName(e.target.value)}
        aria-label="Line name"
      />
      <input
        value={line}
        onChange={(e) => setLine(e.target.value)}
        placeholder="x1,y1,x2,y2 (pixels)"
        aria-label="Line"
      />
      <button type="submit" className="primary" disabled={busy}>
        {busy
          ? "Muse Code is writing and Rulecam is testing… (can take minutes)"
          : "Write and prove"}
      </button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}

/** Live: the owner's request form and every extension's newest version. */
function LiveView() {
  const [records, setRecords] = useState<ExtensionRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    extensions
      .list()
      .then((found) => live && setRecords(found))
      .catch((err: unknown) => live && setError(errorText(err, "Could not load extensions.")));
    return () => {
      live = false;
    };
  }, []);

  const replace = (record: ExtensionRecord): void =>
    setRecords((old) => [record, ...old.filter((r) => r.name !== record.name)]);

  const approve = async (record: ExtensionRecord): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      replace(await extensions.approve(record));
    } catch (err) {
      setError(errorText(err, "Could not approve it."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <RequestForm onMade={replace} />
      {error && <p role="alert">{error}</p>}
      {records.length === 0 && <p className="muted">No extensions yet.</p>}
      {records.map((record) => (
        <ExtensionCard
          key={`${record.name}@${record.sha256}`}
          record={record}
          onApprove={(r) => void approve(r)}
          busy={busy}
        />
      ))}
    </>
  );
}

/** Rule extensions: Muse Code writes new check types; nothing runs until the owner approves. */
export default function ExtensionsPage() {
  const owner = useOwnerControls();
  const { phase } = useAuth();
  const demo = useDemo() === true;
  return (
    <section aria-label="Rule extensions" className="site-page">
      <h2>Rule extensions</h2>
      <Intro />
      {demo ? (
        <SampleView />
      ) : owner ? (
        <LiveView />
      ) : (
        <OwnerOnly checking={phase === "checking"} what="Rule extensions" />
      )}
    </section>
  );
}
