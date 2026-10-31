"use client";

// Who is looking: the site owner (on this machine, or logged in with the owner passphrase) or a
// visitor. Owner-only controls render only for the owner; the API enforces it either way.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { api, setCsrfToken, type AuthStatus } from "./api";
import { useDemo } from "./stream";

export type AuthPhase = "checking" | "owner" | "visitor" | "offline" | "demo";

export interface Auth {
  phase: AuthPhase;
  via: AuthStatus["via"];
  loginEnabled: boolean;
  expiresAt: string | null;
  /** Logs in; throws `ApiError` (401 wrong passphrase, 429 too many tries, 503 not set up). */
  login: (passphrase: string) => Promise<void>;
  logout: () => Promise<void>;
}

type State = Pick<Auth, "phase" | "via" | "loginEnabled" | "expiresAt">;

const CHECKING: State = { phase: "checking", via: null, loginEnabled: false, expiresAt: null };
const noop = async (): Promise<void> => undefined;
const AuthContext = createContext<Auth>({ ...CHECKING, login: noop, logout: noop });

function fromStatus(me: AuthStatus): State {
  return {
    phase: me.owner ? "owner" : "visitor",
    via: me.via,
    loginEnabled: me.login_enabled,
    expiresAt: me.expires_at,
  };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const demo = useDemo();
  const [state, setState] = useState<State>(CHECKING);

  const apply = useCallback((me: AuthStatus): void => {
    setCsrfToken(me.csrf_token);
    setState(fromStatus(me));
  }, []);

  const refresh = useCallback(async (): Promise<void> => {
    try {
      apply(await api.me());
    } catch {
      setCsrfToken(null);
      setState({ ...CHECKING, phase: "offline" });
    }
  }, [apply]);

  useEffect(() => {
    if (demo === null) return;
    if (demo) {
      setState({ ...CHECKING, phase: "demo" });
      return;
    }
    void refresh();
  }, [demo, refresh]);

  const login = useCallback(
    async (passphrase: string): Promise<void> => apply(await api.login(passphrase)),
    [apply],
  );

  const logout = useCallback(async (): Promise<void> => {
    try {
      await api.logout();
    } finally {
      setCsrfToken(null);
      await refresh();
    }
  }, [refresh]);

  const value = useMemo(() => ({ ...state, login, logout }), [state, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  return useContext(AuthContext);
}

/** Whether to show owner-only controls: the owner, or the recorded demo (which calls no API). */
export function useOwnerControls(): boolean {
  const { phase } = useAuth();
  return phase === "owner" || phase === "demo";
}
