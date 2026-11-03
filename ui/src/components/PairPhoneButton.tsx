"use client";

import { useState } from "react";

import { ApiError, withCsrf } from "@/lib/api";
import { API_PROXY } from "@/lib/config";

/** `POST /pair/token`: a single-use pairing link and its QR code. */
export interface PairToken {
  token: string;
  url: string;
  qr_png_base64: string;
  expires_at: string;
}

export async function requestPairToken(): Promise<PairToken> {
  const response = await fetch(`${API_PROXY}/pair/token`, withCsrf({ method: "POST" }));
  if (!response.ok) throw new ApiError(response.status, response.statusText);
  return (await response.json()) as PairToken;
}

/**
 * Pair a phone as a camera in one click: asks the API for a single-use token and shows its QR
 * code and link, so nobody has to call `POST /pair/token` by hand (add-on 5.3).
 */
export function PairPhoneButton({
  request = requestPairToken,
}: {
  request?: () => Promise<PairToken>;
}) {
  const [token, setToken] = useState<PairToken | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const pair = async (): Promise<void> => {
    setBusy(true);
    setMessage("");
    try {
      setToken(await request());
    } catch (e) {
      setMessage(
        e instanceof ApiError && e.status === 403
          ? "Pairing is for the site owner. Log in and try again."
          : "Could not get a pairing link from the Rulecam API.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pair-phone" data-testid="pair-phone">
      <button type="button" onClick={() => void pair()} disabled={busy}>
        {token ? "New pairing link" : "Pair a phone"}
      </button>
      {message && (
        <p className="muted" role="status">
          {message}
        </p>
      )}
      {token && (
        <figure>
          {/* eslint-disable-next-line @next/next/no-img-element -- a data URI, nothing to optimise */}
          <img
            src={`data:image/png;base64,${token.qr_png_base64}`}
            alt="QR code: scan with the phone to pair it"
            width={180}
            height={180}
          />
          <figcaption>
            Scan with the phone, or open <a href={token.url}>{token.url}</a>. Single use, valid
            until {new Date(token.expires_at).toLocaleTimeString()}.
          </figcaption>
        </figure>
      )}
    </div>
  );
}
