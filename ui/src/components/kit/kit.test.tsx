import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AlertCard,
  EmptyState,
  EvidenceCard,
  formatMs,
  LiveBadge,
  MetricTile,
  ModelChip,
  normaliseVerdict,
  shortHash,
  Skeleton,
  StepFlow,
  timeLabel,
  Timeline,
  TimelineItem,
  VerdictPill,
  ZoneDensityBar,
} from "./index";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("LiveBadge", () => {
  it("says LIVE, REPLAY, SAMPLE or OFFLINE in words, never colour alone", () => {
    for (const [mode, word] of [
      ["live", "LIVE"],
      ["replay", "REPLAY"],
      ["sample", "SAMPLE"],
      ["offline", "OFFLINE"],
    ] as const) {
      render(<LiveBadge mode={mode} detail="1 Nov 2026" />);
      const badge = screen.getByTestId("live-badge");
      expect(badge.textContent).toContain(word);
      expect(badge.textContent).toContain("1 Nov 2026");
      expect(badge.getAttribute("data-mode")).toBe(mode);
      expect(badge.getAttribute("role")).toBe("status");
      cleanup();
    }
  });
});

describe("VerdictPill", () => {
  it("folds the verifier's verdicts onto yes, no and unsure", () => {
    expect(normaliseVerdict("violation")).toBe("yes");
    expect(normaliseVerdict("no_violation")).toBe("no");
    expect(normaliseVerdict("uncertain")).toBe("unsure");
    expect(normaliseVerdict(true)).toBe("yes");
    expect(normaliseVerdict(null)).toBe("unsure");
  });

  it("names the role when no model is given, then the verdict and the score", () => {
    render(<VerdictPill verdict="violation" confidence={0.99} />);
    const pill = screen.getByTestId("verdict-pill");
    expect(pill.getAttribute("data-verdict")).toBe("yes");
    expect(pill.textContent).toContain("Tier 1 says violation, P(violation) 0.99");
    expect(pill.textContent).toContain("0.99");
  });

  it("leaves the score out when there is none", () => {
    render(<VerdictPill verdict="no" model="Muse Wonder 1.0" />);
    const pill = screen.getByTestId("verdict-pill");
    expect(pill.textContent).toContain("Muse Wonder 1.0 says no violation");
    expect(pill.textContent).not.toMatch(/\d\.\d\d/);
  });
});

describe("ModelChip", () => {
  it("shows the model, its job and its time", () => {
    render(<ModelChip model="SAM 3.1" job="measured the gap" ms={1840} />);
    const chip = screen.getByTestId("model-chip");
    expect(chip.textContent).toContain("SAM 3.1");
    expect(chip.textContent).toContain("measured the gap");
    expect(chip.textContent).toContain("1.8 s");
  });

  it("says when a model was skipped and greys a non-Meta part", () => {
    render(<ModelChip model="Muse Spark Open" state="skipped" meta={false} />);
    const chip = screen.getByTestId("model-chip");
    expect(chip.textContent).toContain("skipped");
    expect(chip.getAttribute("data-meta")).toBe("false");
    expect(formatMs(640)).toBe("640 ms");
  });
});

describe("StepFlow", () => {
  const steps = [
    { name: "Describe", text: "Say what worries you." },
    { name: "Plan", models: ["Muse Spark 1.3", { model: "SAM 3.1", job: "probes" }] },
    { name: "Prove" },
    { name: "Trust", href: "/demo" },
  ];

  it("is an ordered list with the current step marked", () => {
    render(<StepFlow steps={steps} current={1} label="How a rule goes live" />);
    const list = screen.getByRole("list", { name: "How a rule goes live" });
    const items = within(list).getAllByRole("listitem").filter((li) => li.parentElement === list);
    expect(items).toHaveLength(4);
    expect(items[0]!.getAttribute("data-state")).toBe("done");
    expect(items[1]!.getAttribute("aria-current")).toBe("step");
    expect(items[2]!.getAttribute("data-state")).toBe("todo");
    expect(within(items[1]!).getAllByTestId("model-chip")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Trust" }).getAttribute("href")).toBe("/demo");
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(4);
  });
});

describe("EvidenceCard", () => {
  const hash = "a".repeat(54) + "0123456789";

  it("shows the strip, the facts and a shortened hash that copies in full", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(
      <EvidenceCard
        frames={["/strips/CAM-01_strip_0.jpg", { src: "/strips/CAM-01_strip_1.jpg", alt: "second" }]}
        hash={hash}
        verifyHref="/verify/?day=2026-11-01"
        title="forklift-proximity"
        facts={[{ label: "Camera", value: "CAM-01" }]}
      />,
    );
    expect(screen.getAllByRole("img")).toHaveLength(2);
    expect(screen.getByAltText("second")).toBeTruthy();
    expect(screen.getByTestId("evidence-hash").textContent).toBe(shortHash(hash));
    expect(screen.getByTestId("evidence-hash").getAttribute("title")).toBe(hash);
    expect(screen.getByRole("link", { name: "Verify" }).getAttribute("href")).toBe("/verify/?day=2026-11-01");
    fireEvent.click(screen.getByRole("button", { name: "Copy hash" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy());
    expect(writeText).toHaveBeenCalledWith(hash);
  });

  it("says so when no frames are published", () => {
    render(<EvidenceCard frames={[]} />);
    expect(screen.queryAllByRole("img")).toHaveLength(0);
    expect(screen.getByText("No frames are published for this record.")).toBeTruthy();
  });
});

describe("MetricTile", () => {
  it("shows the figure with its unit and its source", () => {
    render(<MetricTile label="Event to checked decision" value="19.0" unit="s" source={{ href: "/r.json", text: "r.json" }} />);
    const tile = screen.getByTestId("metric-tile");
    expect(tile.textContent).toContain("19.0s");
    expect(screen.getByRole("link", { name: "r.json" }).getAttribute("href")).toBe("/r.json");
  });

  it("reads unknown as unknown, never zero, and can show a skeleton", () => {
    render(<MetricTile label="Cameras" value={null} unit="cams" />);
    expect(screen.getByTestId("metric-tile").textContent).toContain("unknown");
    expect(screen.getByTestId("metric-tile").textContent).not.toContain("cams");
    cleanup();
    render(<MetricTile label="Cameras" value={3} loading />);
    expect(screen.getByTestId("skeleton").getAttribute("aria-busy")).toBe("true");
  });
});

describe("Timeline", () => {
  it("lists entries on one spine with machine-readable times", () => {
    render(
      <Timeline label="Escalation">
        <TimelineItem time="2026-11-01T14:02:09Z" title="Alert sent" tone="instrument" />
        <TimelineItem time="+30 s" title="Supervisor on the way" tone="person" meta="Shift lead" />
      </Timeline>,
    );
    const items = within(screen.getByRole("list", { name: "Escalation" })).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]!.querySelector("time")?.getAttribute("dateTime")).toBe("2026-11-01T14:02:09Z");
    expect(items[0]!.textContent).toContain("14:02:09");
    expect(items[1]!.getAttribute("data-tone")).toBe("person");
    expect(timeLabel("+30 s")).toBe("+30 s");
  });
});

describe("AlertCard", () => {
  it("shows what fired, where, the verdict and the actions", () => {
    render(
      <AlertCard
        title="Person within 2 m of a moving forklift"
        rule="forklift-proximity-2m"
        camera="phone-1"
        time="14:02:09"
        severity="High"
        mode="replay"
        verdict={{ verdict: "violation", confidence: 0.99 }}
        actions={<button type="button">On my way</button>}
      />,
    );
    const card = screen.getByTestId("alert-card");
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe("Person within 2 m of a moving forklift");
    expect(card.getAttribute("data-severity")).toBe("high");
    expect(card.textContent).toContain("forklift-proximity-2m");
    expect(card.textContent).toContain("phone-1");
    expect(within(card).getByTestId("verdict-pill").getAttribute("data-verdict")).toBe("yes");
    expect(within(card).getByTestId("live-badge").textContent).toContain("REPLAY");
    expect(screen.getByRole("button", { name: "On my way" })).toBeTruthy();
  });
});

describe("ZoneDensityBar", () => {
  it("draws one bar per zone, coloured against the site's own limit", () => {
    render(
      <ZoneDensityBar
        zones={[
          { name: "Gate A", density: 1.2 },
          { name: "Gate B", density: 3.1 },
          { name: "Stage front", density: 4.4 },
          { name: "Bar", density: null },
        ]}
        warnAt={3}
        limit={4}
      />,
    );
    const rows = within(screen.getByTestId("zone-density")).getAllByRole("listitem");
    expect(rows.map((r) => r.getAttribute("data-tone"))).toEqual(["ok", "warn", "bad", "none"]);
    expect(screen.getByRole("img", { name: "Stage front: 4.4 people/m², limit 4" })).toBeTruthy();
    expect(rows[3]!.textContent).toContain("no reading");
    expect(screen.getByText("Limit 4 people/m²")).toBeTruthy();
  });

  it("has no tone without a limit", () => {
    render(<ZoneDensityBar zones={[{ name: "Hall", density: 9 }]} />);
    expect(within(screen.getByTestId("zone-density")).getByRole("listitem").getAttribute("data-tone")).toBe("ok");
    expect(screen.queryByText(/^Limit/)).toBeNull();
  });
});

describe("EmptyState and Skeleton", () => {
  it("says what is empty and offers the one action", () => {
    render(
      <EmptyState title="No alerts yet" icon="bell" action={<a href="/planner">Write a rule</a>}>
        Alerts appear here once an approved rule fires.
      </EmptyState>,
    );
    const empty = screen.getByTestId("empty-state");
    expect(empty.textContent).toContain("No alerts yet");
    expect(empty.getAttribute("role")).toBeNull();
    expect(screen.getByRole("link", { name: "Write a rule" })).toBeTruthy();
  });

  it("announces an error", () => {
    render(<EmptyState title="Could not reach the API" tone="error" />);
    expect(screen.getByRole("alert").textContent).toContain("Could not reach the API");
  });

  it("draws the requested number of lines", () => {
    render(<Skeleton lines={3} label="Loading alerts" />);
    const sk = screen.getByRole("status", { name: "Loading alerts" });
    expect(sk.children).toHaveLength(3);
  });
});
