"use client";

import { useCallback, useEffect, useState, type MouseEvent } from "react";

import {
  api,
  errorText,
  type Answer,
  type PlanQuestion,
  type PlanQuestions,
  type Point,
} from "@/lib/api";

/** Known floor shapes for a calibration: the four corners in tap order, in metres. */
export const FLOOR_SHAPES: Record<string, { label: string; ground: Point[] }> = {
  square_1m: {
    label: "1 m square",
    ground: [
      [0, 0],
      [1, 0],
      [1, 1],
      [0, 1],
    ],
  },
  pallet: {
    label: "48 × 40 in pallet",
    ground: [
      [0, 0],
      [1.219, 0],
      [1.219, 1.016],
      [0, 1.016],
    ],
  },
};

interface Draft {
  text: string;
  taps: Point[];
  shape: keyof typeof FLOOR_SHAPES;
}

const EMPTY: Draft = { text: "", taps: [], shape: "square_1m" };
const CORNERS = 4;

/** A click on a scaled `<img>`, in the image's own pixels (what the planner's frames use). */
export function toImagePoint(
  clientX: number,
  clientY: number,
  box: { left: number; top: number; width: number; height: number },
  natural: { width: number; height: number },
): Point | null {
  if (box.width <= 0 || box.height <= 0 || natural.width <= 0 || natural.height <= 0) return null;
  const x = ((clientX - box.left) * natural.width) / box.width;
  const y = ((clientY - box.top) * natural.height) / box.height;
  return [
    Math.round(Math.min(Math.max(x, 0), natural.width)),
    Math.round(Math.min(Math.max(y, 0), natural.height)),
  ];
}

const isCalibration = (q: PlanQuestion): boolean => q.target_field === "calibration";

/** The answer a draft gives, or null when the owner has not answered that question. */
export function toAnswer(question: PlanQuestion, draft: Draft): Answer | null {
  const answer: Answer = { question_id: question.question_id };
  const text = draft.text.trim();
  if (text) answer.text = text;
  if (isCalibration(question)) {
    if (draft.taps.length === CORNERS) {
      answer.calibration = { image_px: draft.taps, ground_m: FLOOR_SHAPES[draft.shape]!.ground };
    }
  } else if (draft.taps[0]) {
    answer.tap = { x: draft.taps[0][0], y: draft.taps[0][1] };
  }
  return answer.text || answer.tap || answer.calibration ? answer : null;
}

function FrameTap({
  src,
  taps,
  onTap,
  label,
}: {
  src: string;
  taps: Point[];
  onTap: (point: Point) => void;
  label: string;
}) {
  const [size, setSize] = useState({ width: 0, height: 0 });
  const click = (event: MouseEvent<HTMLImageElement>): void => {
    const img = event.currentTarget;
    const point = toImagePoint(event.clientX, event.clientY, img.getBoundingClientRect(), {
      width: img.naturalWidth,
      height: img.naturalHeight,
    });
    if (point) onTap(point);
  };
  return (
    <div className="frame-tap">
      {/* eslint-disable-next-line @next/next/no-img-element -- an owner-only API image */}
      <img
        src={src}
        alt={label}
        onClick={click}
        onLoad={(e) =>
          setSize({ width: e.currentTarget.naturalWidth, height: e.currentTarget.naturalHeight })
        }
      />
      {size.width > 0 &&
        taps.map(([x, y], i) => (
          <span
            key={`${x}-${y}-${i}`}
            className="tap-mark"
            style={{ left: `${(100 * x) / size.width}%`, top: `${(100 * y) / size.height}%` }}
          >
            {i + 1}
          </span>
        ))}
    </div>
  );
}

function QuestionItem({
  planId,
  question,
  draft,
  onChange,
}: {
  planId: string;
  question: PlanQuestion;
  draft: Draft;
  onChange: (draft: Draft) => void;
}) {
  const calibration = isCalibration(question);
  const maxTaps = calibration ? CORNERS : 1;
  const tap = (point: Point): void =>
    onChange({ ...draft, taps: [...(maxTaps === 1 ? [] : draft.taps), point].slice(0, maxTaps) });
  return (
    <li className="question" data-testid="question">
      <p>
        <span className="pill">
          {question.step === "ask" ? "Before the rules" : "To check a rule"}
        </span>{" "}
        {question.text}
      </p>
      {question.options.length > 0 && (
        <div className="buttons">
          {question.options.map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={draft.text === option}
              onClick={() => onChange({ ...draft, text: option })}
            >
              {option}
            </button>
          ))}
        </div>
      )}
      {!calibration && (
        <input
          aria-label={`Answer: ${question.text}`}
          maxLength={500}
          value={draft.text}
          onChange={(e) => onChange({ ...draft, text: e.target.value })}
        />
      )}
      {question.requires_frame_tap && question.camera_id && (
        <>
          <p className="muted">
            {calibration
              ? `Tap the four corners of a known shape on ${question.camera_id}'s floor, going around it (${draft.taps.length}/${CORNERS}).`
              : `Tap it on ${question.camera_id}'s picture.`}
          </p>
          {calibration && (
            <div className="buttons">
              {Object.entries(FLOOR_SHAPES).map(([key, shape]) => (
                <button
                  key={key}
                  type="button"
                  aria-pressed={draft.shape === key}
                  onClick={() => onChange({ ...draft, shape: key })}
                >
                  {shape.label}
                </button>
              ))}
              <button type="button" onClick={() => onChange({ ...draft, taps: [] })}>
                Clear taps
              </button>
            </div>
          )}
          <FrameTap
            src={api.frameUrl(planId, question.camera_id)}
            taps={draft.taps}
            onTap={tap}
            label={`Latest frame of ${question.camera_id}`}
          />
        </>
      )}
    </li>
  );
}

/**
 * The planner's open questions for a plan (step 5 ask, step 8 accept): answer in words, by
 * tapping the camera picture, or by tapping a known floor shape to calibrate. Sending them
 * re-runs the planner; the plan comes back one version up and needs approving again.
 */
export function QuestionsPanel({ planId, version }: { planId: string; version?: number }) {
  const [data, setData] = useState<PlanQuestions | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    try {
      setData(await api.questions(planId));
      setError("");
    } catch (e) {
      setError(errorText(e, "Could not load the planner's questions."));
    }
  }, [planId]);

  useEffect(() => {
    setDrafts({});
    void load();
  }, [load, version]);

  if (error) return <p role="alert">{error}</p>;
  if (!data) return <p className="muted">Loading the planner’s questions…</p>;
  if (data.running) {
    return (
      <p className="muted" role="status">
        {message || "The planner is working on this plan; its questions appear when it is done."}
      </p>
    );
  }
  if (data.questions.length === 0) {
    return <p className="muted">{message || "The planner has no open questions."}</p>;
  }

  const answers = data.questions
    .map((q) => toAnswer(q, drafts[q.question_id] ?? EMPTY))
    .filter((a): a is Answer => a !== null);

  const send = async (): Promise<void> => {
    setBusy(true);
    setMessage("");
    try {
      await api.answer(planId, answers);
      setMessage(
        `Re-planning with ${answers.length} answer${answers.length === 1 ? "" : "s"}; the new version will need approving.`,
      );
      setDrafts({});
      await load();
    } catch (e) {
      setMessage(errorText(e, "Could not send the answers."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="questions">
      <ol>
        {data.questions.map((q) => (
          <QuestionItem
            key={q.question_id}
            planId={planId}
            question={q}
            draft={drafts[q.question_id] ?? EMPTY}
            onChange={(d) => setDrafts((prev) => ({ ...prev, [q.question_id]: d }))}
          />
        ))}
      </ol>
      <button
        type="button"
        className="primary"
        disabled={busy || answers.length === 0}
        onClick={send}
      >
        {busy ? "Sending…" : "Send answers and re-plan"}
      </button>
      {message && <p role="status">{message}</p>}
    </div>
  );
}
