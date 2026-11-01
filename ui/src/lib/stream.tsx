"use client";

// Connects the UI to `/ws` (or to the recorded demo frames with `?demo=1`) and shares the folded
// state through React context. Reconnects with a capped backoff; the badge shows OFFLINE meanwhile.

import {
  createContext,
  useContext,
  useEffect,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";

import { demoFrames } from "@/fixtures/demo-frames";

import { API_BASE, wsUrl } from "./config";
import { parseFrame, type ModeChanged } from "./events";
import { initialState, reducer, type Action, type UiState } from "./store";

const StreamContext = createContext<UiState>(initialState);
const DispatchContext = createContext<Dispatch<Action>>(() => undefined);
const MAX_BACKOFF_MS = 10_000;
const DEMO_STEP_MS = 250;

function playDemo(dispatch: Dispatch<Action>): () => void {
  dispatch({ type: "connection", connection: "open" });
  const timers = demoFrames.map((frame, index) =>
    setTimeout(() => dispatch({ type: "frame", frame }), index * DEMO_STEP_MS),
  );
  return () => timers.forEach(clearTimeout);
}

function connect(dispatch: Dispatch<Action>): () => void {
  let socket: WebSocket | null = null;
  let retry: ReturnType<typeof setTimeout> | null = null;
  let backoff = 500;
  let stopped = false;

  const open = (): void => {
    dispatch({ type: "connection", connection: "connecting" });
    socket = new WebSocket(wsUrl());
    socket.onopen = () => {
      backoff = 500;
      dispatch({ type: "connection", connection: "open" });
      fetch(`${API_BASE}/mode`)
        .then((r) => (r.ok ? (r.json() as Promise<ModeChanged>) : null))
        .then((mode) => mode && dispatch({ type: "mode", mode }))
        .catch(() => undefined);
    };
    socket.onmessage = (event: MessageEvent<string>) => {
      const frame = parseFrame(event.data);
      if (frame) dispatch({ type: "frame", frame });
    };
    socket.onclose = () => {
      dispatch({ type: "connection", connection: "closed" });
      if (!stopped) {
        retry = setTimeout(open, backoff);
        backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
      }
    };
  };

  open();
  return () => {
    stopped = true;
    if (retry) clearTimeout(retry);
    socket?.close();
  };
}

export function StreamProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  useEffect(() => {
    const demo = new URLSearchParams(window.location.search).get("demo") === "1";
    return demo ? playDemo(dispatch) : connect(dispatch);
  }, []);
  return (
    <DispatchContext.Provider value={dispatch}>
      <StreamContext.Provider value={state}>{children}</StreamContext.Provider>
    </DispatchContext.Provider>
  );
}

export function useStream(): UiState {
  return useContext(StreamContext);
}

export function useDispatch(): Dispatch<Action> {
  return useContext(DispatchContext);
}

/** True when the page runs on recorded demo frames (`?demo=1`): no API calls are made. */
export function isDemo(): boolean {
  return (
    typeof window !== "undefined" && new URLSearchParams(window.location.search).get("demo") === "1"
  );
}
