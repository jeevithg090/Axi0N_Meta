import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { PairPhoneButton, requestPairToken, type PairToken } from "./PairPhoneButton";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const token: PairToken = {
  token: "abcdefghijklmnop",
  url: "https://tunnel.example/pair/abcdefghijklmnop",
  qr_png_base64: "iVBORw0KGgo=",
  expires_at: "2026-11-02T12:10:00Z",
};

describe("pair a phone", () => {
  it("shows the QR code and link after one click", async () => {
    render(<PairPhoneButton request={() => Promise.resolve(token)} />);
    fireEvent.click(screen.getByRole("button", { name: "Pair a phone" }));
    const qr = await screen.findByAltText(/scan with the phone/);
    expect(qr.getAttribute("src")).toBe("data:image/png;base64,iVBORw0KGgo=");
    expect(screen.getByRole("link").getAttribute("href")).toBe(token.url);
    expect(screen.getByRole("button").textContent).toBe("New pairing link");
  });

  it("asks a visitor to log in", async () => {
    render(<PairPhoneButton request={() => Promise.reject(new ApiError(403, "Forbidden"))} />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Log in"));
    cleanup();
    render(<PairPhoneButton request={() => Promise.reject(new Error("offline"))} />);
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Could not"));
  });

  it("posts to /pair/token through the API proxy", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify(token), { status: 201 })),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(requestPairToken()).resolves.toEqual(token);
    expect(fetchMock).toHaveBeenCalledWith("/rulecam-api/pair/token", { method: "POST" });
    vi.stubGlobal("fetch", () => Promise.resolve(new Response("", { status: 403 })));
    await expect(requestPairToken()).rejects.toBeInstanceOf(ApiError);
  });
});
