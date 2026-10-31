"use client";

// Connects the UI to `/ws` (or to the recorded demo frames with `?demo=1`) and shares the folded
// state through React context. Reconnects with a capped backoff; the badge shows OFFLINE meanwhile.

import { useSearchParams } from "next/navigation";
import {
  createContext,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useReducer,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
} from "react";

import { demoFrames } from "@/fixtures/demo-frames";

import { API_PROXY, wsUrl } from "./config";
import { parseFrame, type ModeChanged } from "./events";
import { initialState, reducer, type Action, type UiState } from "./store";

const StreamContext = createContext<UiState>(initialState);
const DispatchContext = createContext<Dispatch<Action>>(() => undefined);
// null until the URL has been read; then whether the page runs on the recorded demo frames.
const DemoContext = createContext<boolean | null>(null);
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
  const demoRef = useRef<boolean | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  // Switching between the live socket and the recording never mixes their events, modes or
  // plans: the old source stops and the state resets before any screen sees the new `demo`, so
  // screens that load data for the new source (usePlans) are not wiped by the reset.
  const onDemo = useCallback((next: boolean) => {
    if (demoRef.current === next) return;
    demoRef.current = next;
    stopRef.current?.();
    stopRef.current = null;
    dispatch({ type: "reset" });
    setDemo(next);
  }, []);
  useEffect(() => {
    if (demo === null) return undefined;
    const stop = demo ? playDemo(dispatch) : connect(dispatch);
    stopRef.current = stop;
    return () => {
      stop();
      if (stopRef.current === stop) stopRef.current = null;
    };
  }, [demo]);
  return (
    <DispatchContext.Provider value={dispatch}>
      <DemoContext.Provider value={demo}>
        <StreamContext.Provider value={state}>
          <Suspense fallback={null}>
            <DemoParam onChange={onDemo} />
          </Suspense>
          {children}
        </StreamContext.Provider>
      </DemoContext.Provider>
    </DispatchContext.Provider>
  );
}

export function useStream(): UiState {
  return useContext(StreamContext);
}

export function useDispatch(): Dispatch<Action> {
  return useContext(DispatchContext);
}

/**
 * Whether the page runs on recorded demo frames (`?demo=1`), so no API calls are made; `null`
 * until the URL has been read. Follows client-side navigation between demo and live.
 */
export function useDemo(): boolean | null {
  return useContext(DemoContext);
}
