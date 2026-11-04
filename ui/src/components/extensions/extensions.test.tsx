import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SAMPLE_EXTENSION } from "@/fixtures/extension-sample";

import { ExtensionCard } from "./ExtensionCard";

afterEach(cleanup);

const record = SAMPLE_EXTENSION;

describe("ExtensionCard", () => {
  it("shows who wrote it, every proof step, the score and the review", () => {
    render(<ExtensionCard record={record} />);
    expect(screen.getByText(record.request)).toBeTruthy();
    expect(screen.getByTestId("extension-status").textContent).toBe("PROPOSED");
    expect(screen.getByText(/Written by Muse Code/)).toBeTruthy();
    expect(screen.getByText(/Mutation testing/)).toBeTruthy();
    expect(screen.getByText(/Muse Image scenes/)).toBeTruthy();
    expect(screen.getByRole("img", { name: /Mutation score 100%/ })).toBeTruthy();
    expect(screen.getByText(/Reviewed by Muse Spark Open/)).toBeTruthy();
  });

  it("offers approval only when it can be approved", () => {
    const onApprove = vi.fn();
    render(<ExtensionCard record={record} onApprove={onApprove} />);
    fireEvent.click(screen.getByRole("button", { name: "Approve this version" }));
    expect(onApprove).toHaveBeenCalledWith(record);
    cleanup();
    render(<ExtensionCard record={{ ...record, status: "rejected" }} onApprove={onApprove} />);
    expect(
      (screen.getByRole("button", { name: "Cannot be approved" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("tells an approved version's rule state", () => {
    render(<ExtensionCard record={{ ...record, status: "approved" }} />);
    expect(
      screen.getByText(new RegExp(`ext:${record.name}@${record.sha256.slice(0, 12)}`)),
    ).toBeTruthy();
  });
});
