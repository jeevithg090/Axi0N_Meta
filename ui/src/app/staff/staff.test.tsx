import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ItemCard, nextAction } from "./_components/ItemCard";
import { mergeItems, ordered } from "./_lib/feed";
import { WORDS, countdown, duration, roleWords, toRoleId } from "./_lib/i18n";
import type { StaffItem } from "./_lib/types";

function item(overrides: Partial<StaffItem> = {}): StaffItem {
  return {
    item_id: "ev-1",
    source: "rule",
    title: "Alert: Keep aisle 4 clear",
    body: "Camera cam-1 (high)",
    roles: ["aisle_lead"],
    severity: "high",
    camera_id: "cam-1",
    camera_name: "Aisle 4",
    rule_text: "Keep aisle 4 clear",
    measurement: "0.42 m",
    ts: "2026-11-04T01:00:00Z",
    status: "open",
    verdict: {
      model: "muse-glimmer-30b",
      verdict: "violation",
      confidence: 0.91,
      reason: "pallet",
      late: false,
    },
    thumbnails: 2,
    escalation_step: "1/2",
    next_role: "store_manager",
    escalates_at: new Date(Date.now() + 42_000).toISOString(),
    actions: [],
    do_now: [],
    announcements: [],
    sample: false,
    dry_run: false,
    timeline: [
      { stage: "raised", ts: "2026-11-04T01:00:00Z", label: "Raised", role: null, after_s: 0 },
    ],
    ack_s: null,
    fix_s: null,
    ...overrides,
  };
}

describe("staff words", () => {
  it("formats durations, countdowns and roles", () => {
    expect(duration(null)).toBe("–");
    expect(duration(42)).toBe("42 s");
    expect(duration(65)).toBe("1 m 05 s");
    expect(duration(3720)).toBe("1 h 2 m");
    expect(countdown(61_000)).toBe("1:01");
    expect(countdown(-5)).toBe("0:00");
    expect(roleWords("store_manager")).toBe("store manager");
    expect(toRoleId(" Dock Lead ")).toBe("dock_lead");
    expect(toRoleId("1 bad")).toBeNull();
  });

  it("has every word in every language", () => {
    const keys = Object.keys(WORDS.en).sort();
    expect(Object.keys(WORDS.es).sort()).toEqual(keys);
    expect(Object.keys(WORDS.hi).sort()).toEqual(keys);
  });
});

describe("feed order", () => {
  it("keeps one entry per item, open ones first", () => {
    const a = item({ item_id: "a", ts: "2026-11-04T01:00:00Z", status: "done" });
    const b = item({ item_id: "b", ts: "2026-11-04T00:59:00Z" });
    const merged = mergeItems([a], [b, { ...a, title: "new" }]);
    expect(merged.map((i) => i.item_id)).toEqual(["a", "b"]);
    expect(merged[0]?.title).toBe("new");
    expect(ordered(merged).map((i) => i.item_id)).toEqual(["b", "a"]);
  });

  it("offers the next tap in the loop", () => {
    expect(nextAction(item())).toBe("acknowledge");
    expect(nextAction(item({ status: "acknowledged" }))).toBe("on_my_way");
    expect(nextAction(item({ status: "on_my_way" }))).toBe("done");
    expect(nextAction(item({ status: "done" }))).toBeNull();
  });
});

describe("ItemCard", () => {
  it("shows the verdict, the countdown and big actions in the chosen language", () => {
    const onAct = vi.fn();
    render(
      <ItemCard
        item={item()}
        words={WORDS.es}
        fresh={false}
        busy={false}
        translated="Pasillo 4: retire el palé"
        message={null}
        onAct={onAct}
        onRead={() => {}}
      />,
    );
    expect(screen.getByText("Pasillo 4: retire el palé")).toBeTruthy();
    expect(screen.getByText(/violation · 91%/)).toBeTruthy();
    expect(screen.getByRole("timer").textContent).toContain("store manager");
    screen.getByRole("button", { name: "Recibido" }).click();
    expect(onAct).toHaveBeenCalledWith(expect.objectContaining({ item_id: "ev-1" }), "acknowledge");
    expect(screen.getAllByRole("img")).toHaveLength(2);
  });
});
