import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { VoiceReport } from "./VoiceReport";

describe("worker page voice report", () => {
  it("speaks the page's language and asks for no name", () => {
    const { unmount } = render(<VoiceReport lang="es" />);
    expect(screen.getByRole("heading", { name: /casi accidente/ })).toBeTruthy();
    expect(screen.getByText(/No hace falta su nombre/)).toBeTruthy();
    unmount();
    render(<VoiceReport lang="en" />);
    expect(screen.getByRole("button", { name: "Start recording" })).toBeTruthy();
  });
});
