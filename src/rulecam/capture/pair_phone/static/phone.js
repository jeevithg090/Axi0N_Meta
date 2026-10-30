// Rulecam Pair-a-Phone page (task P1-09): this phone becomes a camera.
//
// Video is recorded in 3 s clips, one MediaRecorder run per clip (start, stop after 3 s, start
// again). A single recorder with a timeslice would emit fragments of one stream, and only the
// first fragment carries the container header, so the server could not read the others alone.
"use strict";

(() => {
  const CLIP_MS = 3000;
  const COACH_RETRY_MS = 3000;
  const OFFLINE_RETRY_MS = 1000;
  const MAX_QUEUED_CLIPS = 3;
  const VIDEO_BITS_PER_SECOND = 1500000;
  const RECORDER_TYPES = ["video/webm;codecs=vp8", "video/webm;codecs=vp9", "video/webm", "video/mp4"];

  const base = location.pathname.replace(/\/+$/, "");
  const storageKey = `rulecam-pair:${base}`;
  const byId = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const state = {
    session: null,
    stream: null,
    recorder: null,
    running: false,
    queue: [],
    uploading: false,
    sent: 0,
    socket: null,
    wakeLock: null,
  };

  function setStatus(kind, text) {
    byId("status").className = `status status-${kind}`;
    byId("status-text").textContent = text;
  }

  function fail(text) {
    setStatus("error", text);
  }

  function showProgress() {
    const clips = `${state.sent} ${state.sent === 1 ? "clip" : "clips"}`;
    if (state.running) {
      setStatus("live", `Live: ${clips} sent`);
    } else if (!byId("status").classList.contains("status-error")) {
      setStatus("idle", `Stopped after ${clips}. Press Start camera to resume.`);
    }
  }

  function loadPairing() {
    try {
      const saved = sessionStorage.getItem(storageKey);
      return saved ? JSON.parse(saved) : null;
    } catch {
      return null;
    }
  }

  function savePairing(paired) {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(paired));
    } catch {
      // Private mode: pairing still works until the page is reloaded.
    }
  }

  async function errorText(response) {
    try {
      const body = await response.json();
      return typeof body.detail === "string" ? body.detail : `error ${response.status}`;
    } catch {
      return `error ${response.status}`;
    }
  }

  async function claim(label) {
    const saved = loadPairing();
    if (saved) return saved;
    const response = await fetch(`${base}/claim`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label }),
    });
    if (!response.ok) throw new Error(await errorText(response));
    const paired = await response.json();
    savePairing(paired);
    return paired;
  }

  function pickRecorderType() {
    if (typeof MediaRecorder === "undefined") return null;
    return RECORDER_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) ?? "";
  }

  function recordClip(mimeType) {
    if (!state.running) return;
    const parts = [];
    const options = { videoBitsPerSecond: VIDEO_BITS_PER_SECOND };
    if (mimeType) options.mimeType = mimeType;
    const recorder = new MediaRecorder(state.stream, options);
    state.recorder = recorder;
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) parts.push(event.data);
    };
    recorder.onstop = () => {
      if (parts.length) enqueue(new Blob(parts, { type: recorder.mimeType || mimeType }));
      recordClip(mimeType);
    };
    recorder.start();
    setTimeout(() => {
      if (recorder.state === "recording") recorder.stop();
    }, CLIP_MS);
  }

  function enqueue(clip) {
    if (state.queue.length >= MAX_QUEUED_CLIPS) {
      state.queue.shift();
      setStatus("warn", "Slow network: skipping old clips");
    }
    state.queue.push(clip);
    if (!state.uploading) drainQueue();
  }

  async function drainQueue() {
    state.uploading = true;
    while (state.queue.length) {
      const clip = state.queue[0];
      const outcome = await upload(clip);
      if (outcome === "retry") continue;
      const index = state.queue.indexOf(clip);
      if (index >= 0) state.queue.splice(index, 1);
      if (outcome === "fatal") {
        stop();
        break;
      }
    }
    state.uploading = false;
  }

  async function upload(clip) {
    const form = new FormData();
    form.append("file", clip, clip.type.startsWith("video/mp4") ? "clip.mp4" : "clip.webm");
    let response;
    try {
      response = await fetch(`${base}/chunk`, {
        method: "POST",
        headers: { "X-Pair-Session": state.session },
        body: form,
      });
    } catch {
      setStatus("warn", "Offline: retrying");
      await sleep(OFFLINE_RETRY_MS);
      return "retry";
    }
    if (response.status === 429) {
      await sleep(1000 * Number(response.headers.get("Retry-After") || 1));
      return "retry";
    }
    if (response.ok) {
      state.sent += 1;
      showProgress();
      return "ok";
    }
    const reason = await errorText(response);
    if ([401, 404, 410].includes(response.status)) {
      fail(`Pairing ended: ${reason}`);
      return "fatal";
    }
    setStatus("warn", `Clip rejected: ${reason}`);
    return "skipped";
  }

  function verdict(confidence) {
    if (confidence >= 0.7) return ["good", "Good"];
    if (confidence >= 0.4) return ["weak", "Weak"];
    return ["none", "Not seen"];
  }

  function barItem(concept, confidence) {
    const [kind, label] = verdict(confidence);
    const item = document.createElement("li");
    item.className = "bar";
    const name = document.createElement("span");
    name.textContent = concept;
    const meter = document.createElement("meter");
    Object.assign(meter, { min: 0, max: 1, low: 0.4, high: 0.7, optimum: 1, value: confidence });
    meter.setAttribute("aria-label", `${concept} confidence`);
    const text = document.createElement("span");
    text.className = `verdict verdict-${kind}`;
    text.textContent = `${label} ${Math.round(confidence * 100)}%`;
    item.append(name, meter, text);
    return item;
  }

  function onCoachMessage(message) {
    const status = byId("coach-status");
    if (message.type === "hello") {
      status.textContent = message.concepts.length
        ? `Checking: ${message.concepts.join(", ")}`
        : "Nothing to check yet: the plan has no concepts.";
    } else if (message.type === "status") {
      status.textContent = message.text;
    } else if (message.type === "bars") {
      const entries = Object.entries(message.bars);
      byId("bars").replaceChildren(...entries.map(([concept, value]) => barItem(concept, value)));
      status.textContent = entries.length
        ? "Move the phone until every bar says Good."
        : "Nothing to check yet: the plan has no concepts.";
    } else if (message.type === "hint") {
      byId("hint").hidden = false;
      byId("hint").textContent = message.text;
    }
  }

  function connectCoach() {
    if (!state.running) return;
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${scheme}//${location.host}${base}/coach`);
    state.socket = socket;
    socket.onopen = () => socket.send(JSON.stringify({ session: state.session }));
    socket.onmessage = (event) => onCoachMessage(JSON.parse(event.data));
    socket.onclose = () => {
      if (!state.running) return;
      byId("coach-status").textContent = "Coach reconnecting…";
      setTimeout(connectCoach, COACH_RETRY_MS);
    };
  }

  async function keepAwake() {
    try {
      state.wakeLock = navigator.wakeLock ? await navigator.wakeLock.request("screen") : null;
    } catch {
      state.wakeLock = null;
    }
  }

  async function start(event) {
    event.preventDefault();
    if (!window.isSecureContext || !navigator.mediaDevices) {
      fail("Open this page over HTTPS (scan the QR code again).");
      return;
    }
    const mimeType = pickRecorderType();
    if (mimeType === null) {
      fail("This browser cannot record video.");
      return;
    }
    byId("start").disabled = true;
    setStatus("idle", "Pairing…");
    let paired;
    try {
      paired = await claim(byId("camera-name").value.trim());
      state.stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } },
      });
    } catch (error) {
      byId("start").disabled = false;
      fail(error instanceof Error ? error.message : String(error));
      return;
    }
    state.session = paired.session;
    byId("preview").srcObject = state.stream;
    byId("camera-id").textContent = `${paired.label} (${paired.camera_id})`;
    byId("setup").hidden = true;
    byId("live").hidden = false;
    state.running = true;
    setStatus("live", "Live: recording the first clip");
    recordClip(mimeType);
    connectCoach();
    keepAwake();
  }

  function stop() {
    state.running = false;
    if (state.recorder && state.recorder.state === "recording") state.recorder.stop();
    if (state.stream) state.stream.getTracks().forEach((track) => track.stop());
    if (state.socket) state.socket.close();
    if (state.wakeLock) state.wakeLock.release();
    byId("live").hidden = true;
    byId("setup").hidden = false;
    byId("start").disabled = false;
    showProgress();
  }

  document.addEventListener("visibilitychange", () => {
    if (state.running && document.visibilityState === "visible") keepAwake();
  });

  document.addEventListener("DOMContentLoaded", () => {
    byId("setup").addEventListener("submit", start);
    byId("stop").addEventListener("click", stop);
    const saved = loadPairing();
    if (saved) byId("camera-name").value = saved.label;
  });
})();
