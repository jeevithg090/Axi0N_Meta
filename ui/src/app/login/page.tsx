"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function loginError(error: unknown): string {
  if (!(error instanceof ApiError)) return "Could not reach the Rulecam API. Try again.";
  if (error.status === 401) return "That passphrase is not right.";
  if (error.status === 429) return "Too many attempts. Wait a minute, then try again.";
  if (error.status === 503) return "Owner login is not set up on this server yet.";
  return error.message;
}

/** The owner logs in with the passphrase set on the API server (`rulecam owner set-passphrase`). */
export default function LoginPage() {
  const auth = useAuth();
  const router = useRouter();
  const [passphrase, setPassphrase] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (auth.phase === "owner") {
    return (
      <section className="login" aria-label="Owner login">
        <h2>Owner</h2>
        <p role="status">
          {auth.via === "loopback"
            ? "You are on the owner's machine: no login needed."
            : "You are logged in as the owner."}
        </p>
        <p>
          <Link href="/planner">Go to the planner →</Link>
        </p>
      </section>
    );
  }

  const submit = async (event: FormEvent): Promise<void> => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await auth.login(passphrase);
      setPassphrase("");
      router.push("/planner");
    } catch (e) {
      setError(loginError(e));
    } finally {
      setBusy(false);
    }
  };

  const off = auth.phase === "visitor" && !auth.loginEnabled;
  return (
    <section className="login" aria-label="Owner login">
      <h2>Owner login</h2>
      <p className="muted">
        Planning, approving plans, answering the planner and the full evidence log are for the site
        owner. Everyone else can watch the live monitor and check the evidence hashes.
      </p>
      {auth.phase === "offline" && (
        <p role="alert">Could not reach the Rulecam API. Check your connection and reload.</p>
      )}
      {off && <p role="alert">Owner login is not set up on this server yet.</p>}
      <form onSubmit={submit} className="login-form">
        <label htmlFor="passphrase">Passphrase</label>
        <input
          id="passphrase"
          name="passphrase"
          type="password"
          autoComplete="current-password"
          required
          maxLength={1024}
          value={passphrase}
          onChange={(e) => setPassphrase(e.target.value)}
        />
        <button type="submit" className="primary" disabled={busy || off || !passphrase}>
          {busy ? "Checking…" : "Log in"}
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
