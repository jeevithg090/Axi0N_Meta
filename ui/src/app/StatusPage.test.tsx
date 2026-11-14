import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import NotFound from "./not-found";

describe("NotFound", () => {
  it("names the problem and links back to the start page and the replay", () => {
    render(<NotFound />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/no page/i);
    const links = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(links).toEqual(["/", "/demo?demo=1"]);
  });
});
