import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { age, receiptHolds, sha256Hex, speechLang, type RetailCard } from "./api";
import SupermarketPage from "./page";
import { AdviceCard, FindingCard } from "./StoryPanels";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function answering(status: number, detail: string) {
  return vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail }), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

describe("page states", () => {
  it("tells a visitor the page is for the owner", async () => {
    vi.stubGlobal("fetch", answering(403, "owner only"));
    render(<SupermarketPage />);
    await waitFor(() => expect(screen.getByText(/Owner login required/)).toBeTruthy());
    expect(screen.getByText("Log in as the owner").getAttribute("href")).toBe("/login");
  });

  it("says when the API has no supermarket routes", async () => {
    vi.stubGlobal("fetch", answering(404, "Not Found"));
    render(<SupermarketPage />);
    await waitFor(() => expect(screen.getByText(/no supermarket routes yet/)).toBeTruthy());
  });

  it("says when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<SupermarketPage />);
    await waitFor(() => expect(screen.getByText(/Failed to fetch|unreachable/)).toBeTruthy());
  });
});

describe("receipts", () => {
  it("re-hashes the logged text in the browser", async () => {
    const prev = "0".repeat(64);
    const hashed = '{"answer":"Seis.","question":"¿Cuántos?"}';
    const receipt = await sha256Hex(prev + hashed);
    expect(await receiptHolds({ receipt, prev_sha256: prev, hashed_text: hashed })).toBe(true);
    expect(
      await receiptHolds({
        receipt,
        prev_sha256: prev,
        hashed_text: hashed.replace("Seis", "Siete"),
      }),
    ).toBe(false);
  });
});

describe("helpers", () => {
  it("formats ages and speech languages", () => {
    const now = new Date("2026-11-04T10:00:00Z");
    expect(age("2026-11-04T09:59:30Z", now)).toBe("30 s ago");
    expect(age("2026-11-04T09:45:00Z", now)).toBe("15 min ago");
    expect(age(null, now)).toBe("");
    expect(speechLang("Spanish")).toBe("es-ES");
    expect(speechLang("hi")).toBe("hi");
    expect(speechLang("Klingon")).toBe("en-US");
  });
});

const card: RetailCard = {
  recommendation: {
    id: "adv-retail-1",
    status: "measuring",
    do: [{ op: "add_capacity", params: {} }],
    who_role: "shift lead",
    revert: "close the till",
    expect: { interval: [10, 40], unit: "s", model_rung: "allen_cunneen" },
  },
  title: "Open a second till at the morning peak",
  saw: ["The queue reached 6 customers with 1 till open."],
  change: "Open till 2 from 08:30.",
  gain: "Queueing models put the wait at about 60 s with 1 till and 5-9 s with 2.",
  numbers: [
    {
      id: "peak",
      label: "peak queue",
      value: 6,
      unit: "customers",
      definition: "d",
      grounded: true,
    },
    { id: "x", label: "made up", value: 4242, unit: "", definition: "d", grounded: false },
  ],
  hidden: ["x"],
  plan: {
    id: "mp-adv-retail-1",
    primary_metric: "customers waiting",
    guard_metrics: [],
    design: "switchback",
    block_minutes: 30,
    n_pairs: 6,
    min_detectable_effect: 1.5,
    smallest_possible_p: 0.03125,
    decision_rule: "worked: the whole interval is below 0.",
    preregistration_hash: "f".repeat(64),
  },
  sigma_note: "standard deviation of 5 earlier readings",
  watch: { kind: "queue", camera_id: "phone-1", every_s: 60, role: "tills", focus: "" },
  ask_receipt: "a".repeat(64),
  question: "Do we need another till?",
  created_at: "2026-11-04T09:00:00Z",
  seconds: 12,
  preregistered_seq: 7,
  watch_id: "w-1",
};

describe("advice card", () => {
  it("shows grounded numbers, the what-if and the pre-registration", () => {
    render(<AdviceCard card={card} />);
    expect(screen.getByText("Open a second till at the morning peak")).toBeTruthy();
    expect(screen.getByText("✗ hidden")).toBeTruthy();
    expect(screen.getByText(/record #7/)).toBeTruthy();
    expect(screen.getByText(/Queueing models/)).toBeTruthy();
  });
});

describe("finding card", () => {
  it("shows what staff were told", () => {
    render(
      <FindingCard
        finding={{
          id: "f-1",
          kind: "hazard",
          camera_id: "phone-4",
          camera_name: "aisle 3",
          at: "2026-11-04T09:00:00Z",
          checked_at: "2026-11-04T09:00:05Z",
          watch_id: null,
          act: true,
          headline: "1 confirmed hazard(s): spill",
          staff_message: "Bring the mop.",
          detail: { summary: "Milk on the floor." },
          evidence: { frames: ["a.jpg"], marked: "a-marked.jpg", clip: null },
          calls: [{ model: "muse-spark-1.3", role: "hazard finder", seconds: 4.2, error: null }],
          seconds: 9.1,
          alert_id: "al-1",
          task_id: "t-1",
          metric: 1,
          alert_status: "acknowledged",
        }}
      />,
    );
    expect(screen.getByText("Bring the mop.")).toBeTruthy();
    expect(screen.getByText("acknowledged")).toBeTruthy();
    expect(screen.getByAltText("hazard evidence")).toBeTruthy();
  });
});
