import { afterEach, describe, expect, it, vi } from "vitest";

import { setCsrfToken } from "./api";
import {
  markedUrl,
  site,
  sourceNote,
  worstLevel,
  type ContextItem,
  type CrowdReport,
} from "./site";

const item = (over: Partial<ContextItem> = {}): ContextItem => ({
  kind: "recall",
  title: "Recall",
  summary: "s",
  when: "",
  source_url: "https://alerts.food.gov.uk/a",
  source_title: "FSA",
  watch: "",
  cited: false,
  relinked: false,
  ...over,
});

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
});

describe("site tools", () => {
  it("says how a source was checked", () => {
    expect(sourceNote(item())).toBe("retrieved by the search");
    expect(sourceNote(item({ cited: true }))).toBe("retrieved and cited");
    expect(sourceNote(item({ relinked: true, cited: true }))).toMatch(/picked from the pages/);
  });

  it("builds the marked image URL from the report's file name", () => {
    const report = { marked_image: "/data/shelf/marked/shelf-1.jpg" };
    expect(markedUrl(report as never)).toMatch(/\/shelf\/marked\/shelf-1\.jpg$/);
    expect(markedUrl({ marked_image: null } as never)).toBeNull();
  });

  it("finds the worst crowd level", () => {
    const ep = (level: string) => ({ episode: { level } });
    expect(worstLevel({ episodes: [] } as unknown as CrowdReport)).toBe("normal");
    expect(
      worstLevel({
        episodes: [ep("crowded"), ep("critical"), ep("busy")],
      } as unknown as CrowdReport),
    ).toBe("critical");
  });

  it("treats a missing context as none yet, and sends the CSRF token on refresh", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "no festival context yet" }), { status: 404 }),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify({ items: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "boom" }), { status: 502 }));
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken("tok");
    expect(await site.latestContext("festival")).toBeNull();
    await site.refreshContext("supermarket", "Oxford Road");
    const init = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("tok");
    expect(init.body).toBe(JSON.stringify({ notes: "Oxford Road" }));
    await expect(site.latestContext("general")).rejects.toThrow("boom");
  });
});
