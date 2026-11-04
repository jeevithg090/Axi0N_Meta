import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { AskSignage, unreachableText } from "./AskSignage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AskSignage", () => {
  it("starts empty: no scripted conversation", () => {
    render(<AskSignage />);
    expect(screen.getByText(/No questions yet/)).toBeTruthy();
  });

  it("says the API is unreachable instead of inventing an answer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<AskSignage />);
    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "How busy?" } });
    fireEvent.click(screen.getByText("Ask →"));
    await waitFor(() => expect(screen.getByText(/Failed to fetch|unreachable/)).toBeTruthy());
    expect(screen.queryByText(/muse-spark-open/)).toBeNull();
  });

  it("explains API errors in plain words", () => {
    expect(unreachableText(new ApiError(503, "asking needs prototype mode"))).toContain(
      "cannot answer",
    );
    expect(unreachableText(new ApiError(403, "forbidden"))).toContain("log in");
    expect(unreachableText(null)).toContain("unreachable");
  });
});
