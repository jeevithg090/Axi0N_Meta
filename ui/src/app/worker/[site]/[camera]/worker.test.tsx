import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import WorkerTransparencyPage from "./page";

afterEach(cleanup);

describe("WorkerTransparencyPage", () => {
  it("renders transparency notice, never list, and language switcher", async () => {
    render(
      <WorkerTransparencyPage
        params={{ site: "dock-site", camera: "cam1" }}
      />
    );

    // Header & site
    expect(await screen.findByText(/dock-site · cam1/i)).toBeDefined();

    // Never list
    expect(screen.getByText(/The Never List/i)).toBeDefined();
    expect(screen.getByText(/No facial recognition/i)).toBeDefined();
    expect(screen.getByText(/No worker names/i)).toBeDefined();

    // Switch to Spanish
    const esBtn = screen.getByRole("button", { name: "ES" });
    fireEvent.click(esBtn);

    expect(await screen.findByText(/Reglas Transparentes/i)).toBeDefined();
    expect(screen.getByText(/Lo que NUNCA se registra/i)).toBeDefined();
  });

  it("handles tap dispute and updates count in Spanish (UC2)", async () => {
    // Mock fetch for dispute submission
    vi.spyOn(global, "fetch").mockImplementation(async (url, init) => {
      if (typeof url === "string" && url.includes("/dispute") && init?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            dispute_id: "disp-12345",
            event_code: "ev-strap-01",
            outcome: "upheld",
            reviewer_reasoning: "Dispute upheld: Visual confounder identified",
            updated_counts: { alerts: 8, disputes: 3, upheld: 2 },
          }),
        } as Response;
      }
      return {
        ok: false,
      } as Response;
    });

    render(
      <WorkerTransparencyPage
        params={{ site: "dock-site", camera: "cam1" }}
      />
    );

    // Switch to Spanish
    const esBtn = await screen.findByRole("button", { name: "ES" });
    fireEvent.click(esBtn);

    // Find tap dispute button
    const disputeBtn = await screen.findByRole("button", { name: /Disputar Alerta/i });
    fireEvent.click(disputeBtn);

    // Modal opens
    expect(await screen.findByText(/Disputar Alerta Anónima/i)).toBeDefined();

    // Submit dispute
    const submitBtn = screen.getByRole("button", { name: /Enviar Disputa/i });
    fireEvent.click(submitBtn);

    // Verify confirmation and upheld message
    await waitFor(() => {
      expect(screen.getByText(/Disputa aceptada y recuento actualizado/i)).toBeDefined();
    });
  });
});
