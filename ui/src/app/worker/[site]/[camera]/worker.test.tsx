import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { workerPage } from "@/fixtures/worker-page";
import type { DisputeRequest, WorkerPage } from "@/lib/worker";

import WorkerRulesPage from "./page";

vi.mock("next/navigation", () => ({
  useParams: () => ({ site: "main-st-kitchen", camera: "cam1" }),
  useSearchParams: () => new URLSearchParams("lang=es"),
}));

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

let page: WorkerPage;
let disputes: DisputeRequest[];
let disputeStatus: number;

beforeEach(() => {
  page = workerPage();
  disputes = [];
  disputeStatus = 200;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/worker/dispute") && init?.method === "POST") {
      disputes.push(JSON.parse(String(init.body)) as DisputeRequest);
      if (disputeStatus !== 200) return json({ detail: "no" }, disputeStatus);
      page = workerPage({
        counts: { strap: { alerts: 3, disputes: 2, upheld: 2 } },
        events: page.events.map((e, i) => (i === 0 ? { ...e, dispute: "upheld" } : e)),
      });
      return json({
        code: "1A2B3C4D",
        rule_id: "strap",
        outcome: "upheld",
        counts: page.counts.strap,
      });
    }
    const lang = new URL(url, "http://ui").searchParams.get("lang") ?? "es";
    return json({ ...page, lang });
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("worker rules page", () => {
  it("loads the page through the API proxy in the QR's language", async () => {
    render(<WorkerRulesPage />);
    expect(
      await screen.findByText("Abroche el arnés.", { selector: ".worker-rule p" }),
    ).toBeDefined();
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe(
      "/rulecam-api/worker/main-st-kitchen/cam1?lang=es",
    );
    expect(screen.getByText("Sin reconocimiento facial")).toBeDefined();
    expect(screen.getByText(/se guardan 14 días/)).toBeDefined();
    expect(screen.getByText("Aún no descrita en este idioma.")).toBeDefined();
  });

  it("switches language", async () => {
    render(<WorkerRulesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "EN" }));
    expect(await screen.findByText("No face recognition")).toBeDefined();
    expect(String(vi.mocked(fetch).mock.calls.at(-1)?.[0])).toContain("lang=en");
    expect(screen.getByRole("button", { name: "EN" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("sends a Spanish tap dispute and shows the upheld count (UC2)", async () => {
    render(<WorkerRulesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Disputar 1A2B3C4D" }));
    const sheet = screen.getByRole("dialog", { name: "Disputar alerta 1A2B3C4D" });
    fireEvent.change(within(sheet).getByRole("textbox"), { target: { value: "era mi mochila" } });
    fireEvent.click(within(sheet).getByRole("button", { name: "Enviar disputa" }));

    expect(await within(sheet).findByRole("status")).toHaveProperty(
      "textContent",
      "Aceptada: la alerta fue una falsa alarma. Los recuentos se actualizaron.",
    );
    expect(disputes).toEqual([
      { event_code: "1A2B3C4D", reason_choice: "lookalike_object", reason_text: "era mi mochila" },
    ]);
    expect(await screen.findAllByText("Disputa aceptada")).toHaveLength(2);
    const counts = screen.getAllByRole("definition").map((d) => d.textContent);
    expect(counts.slice(0, 3)).toEqual(["3", "2", "2"]);

    fireEvent.click(within(sheet).getByRole("button", { name: "Cerrar" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says when the daily dispute limit is reached, without claiming success", async () => {
    disputeStatus = 429;
    render(<WorkerRulesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Disputar 1A2B3C4D" }));
    fireEvent.click(screen.getByRole("button", { name: "Enviar disputa" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Demasiadas disputas hoy. Inténtelo mañana.",
    );
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows an error instead of made-up data when the API is unreachable", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("offline"));
    render(<WorkerRulesPage />);
    expect(await screen.findByText(/No se pudo cargar/)).toBeDefined();
    expect(screen.queryByText("Abroche el arnés.")).toBeNull();
  });
});
