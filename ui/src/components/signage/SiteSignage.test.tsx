import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SiteSignage } from "./SiteSignage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SiteSignage", () => {
  it("labels the recorded runs it shows before a live result", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<SiteSignage />);
    await waitFor(() => expect(screen.getAllByTestId("sample-data-tag").length).toBe(3));
    expect(screen.getByText(/not today's search/)).toBeTruthy();
    expect(screen.queryByText(/✓ Loaded verified/)).toBeNull();
  });
});
