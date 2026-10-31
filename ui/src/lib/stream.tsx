"use client";

// Connects the UI to `/ws` (or to the recorded demo frames with `?demo=1`) and shares the folded
// state through React context. Reconnects with a capped backoff; the badge shows OFFLINE meanwhile.

import { useSearchParams } from "next/navigation";
import {
  createContext,
  Suspense,
  useContext,
  useEffect,
  useReducer,
  useState,
  type Dispatch,
  type ReactNode,
} from "react";

import { demoFrames } from "@/fixtures/demo-frames";

import { API_PROXY, wsUrl } from "./config";
import { parseFrame, type ModeChanged } from "./events";
import { initialState, reducer, type Action, type UiState } from "./store";

const StreamContext = createContext<UiState>(initialState);
const MAX_BACKOFF_MS = 10_000;
const DEMO_STEP_MS = 600;

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
      fetch(`${API_PROXY}/mode`)
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
    if (socket) {
      // Detach first: this socket's late close event must not mark a newer connection OFFLINE.
      socket.onopen = null;
      socket.onmessage = null;
      socket.onclose = null;
      socket.close();
    }
  };
}

/** Reports whether the URL asks for the recorded demo, on first load and on client navigation. */
function DemoParam({ onChange }: { onChange: (demo: boolean) => void }) {
  const demo = useSearchParams().get("demo") === "1";
  useEffect(() => onChange(demo), [demo, onChange]);
  return null;
}

export function StreamProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [demo, setDemo] = useState<boolean | null>(null);
  useEffect(() => {
    if (demo === null) return undefined;
    // Switching between the live socket and the recording never mixes their events or modes.
    dispatch({ type: "reset" });
    return demo ? playDemo(dispatch) : connect(dispatch);
  }, [demo]);
  return (
    <StreamContext.Provider value={state}>
      <Suspense fallback={null}>
        <DemoParam onChange={setDemo} />
      </Suspense>
      {children}
    </StreamContext.Provider>
  );
}

export function useStream(): UiState {
  return useContext(StreamContext);
}
