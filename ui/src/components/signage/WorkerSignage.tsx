"use client";

import React, { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api";
import { API_PROXY } from "../../lib/config";
import {
  DISPUTE_REASONS,
  STRINGS,
  type DisputeReason,
  type DisputeResult,
  type WorkerPage,
} from "../../lib/worker";

const STATIONS = [
  { cameraId: "CAM-02", label: "CAM-02 · Main Aisle B2 Intersection" },
  { cameraId: "CAM-04", label: "CAM-04 · Staging Bay C4 (Forklift Cab)" },
  { cameraId: "CAM-03", label: "CAM-03 · East Visitor & PPE Corridor" },
  { cameraId: "CAM-05", label: "CAM-05 · Commercial Kitchen Prep Line A" },
  { cameraId: "CAM-06", label: "CAM-06 · West Assembly Zone" },
  { cameraId: "CAM-08", label: "CAM-08 · South Freight Gate" },
  { cameraId: "CAM-09", label: "CAM-09 · Yard Heavy Equipment Bay" },
  { cameraId: "CAM-01", label: "CAM-01 · North Loading Dock" },
];

export function WorkerSignage() {
  // Empty until `GET /plans` names the approved plan: never a built-in plan id.
  const [planId, setPlanId] = useState<string>("");
  const [cameraId, setCameraId] = useState<string>("CAM-04");
  const [lang, setLang] = useState<"en" | "es">("en");
  const [page, setPage] = useState<WorkerPage | null>(null);
  const [selectedCode, setSelectedCode] = useState<string>("");
  const [reasonChoice, setReasonChoice] = useState<DisputeReason>("rule_was_followed");
  const [reasonText, setReasonText] = useState<string>("");
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [lastResult, setLastResult] = useState<DisputeResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadWorkerPage = useCallback(
    async (targetPlan: string, targetCam: string, targetLang: "en" | "es") => {
      setErrorMsg(null);
      try {
        const data = await api.workerPage(targetPlan, targetCam, targetLang);
        setPage(data);
        const firstEv = data.events[0];
        if (firstEv) {
          setSelectedCode(firstEv.code);
        } else {
          setSelectedCode("");
        }
      } catch (err) {
        setErrorMsg(errorText(err, "Could not load worker station card"));
      }
    },
    [],
  );

  useEffect(() => {
    // Check URL query parameter ?camera= if navigated from /streams
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      const qCam = params.get("camera");
      if (qCam) setCameraId(qCam);
    }
    api
      .plans()
      .then((list) => {
        const approved = list.find((p) => p.approved) ?? list[0];
        if (approved) setPlanId(approved.plan_id);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!planId) return;
    void loadWorkerPage(planId, cameraId, lang);
  }, [planId, cameraId, lang, loadWorkerPage]);

  const handleSubmitDispute = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedCode) return;
    setSubmitting(true);
    setErrorMsg(null);
    setLastResult(null);
    try {
      const result = await api.dispute({
        event_code: selectedCode,
        reason_choice: reasonChoice,
        reason_text: reasonText.trim(),
      });
      setLastResult(result);
      setReasonText("");
      await loadWorkerPage(planId, cameraId, lang);
    } catch (err) {
      setErrorMsg(errorText(err, "Failed to submit anonymous worker dispute"));
    } finally {
      setSubmitting(false);
    }
  };

  const isEn = lang === "en";

  return (
    <div style={{ padding: "var(--s4) var(--gut) var(--s6)" }}>
      {/* Top Station Selector Bar */}
      <div
        style={{
          maxWidth: "880px",
          margin: "0 auto var(--s4)",
          padding: "14px 18px",
          background: "var(--paper-2)",
          border: "1px solid var(--color-rule)",
          borderRadius: "var(--radius-card)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "12px",
        }}
      >
        <div>
          <div className="mono" style={{ fontSize: "11px" }}>
            PUBLIC WORKER TRANSPARENCY &amp; DISPUTE PORTAL · GET /worker/{planId}/{cameraId}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", marginTop: "6px", flexWrap: "wrap" }}>
            <label htmlFor="station-select" className="small" style={{ fontWeight: 700 }}>
              {isEn ? "Select Camera Station:" : "Seleccionar Estación:"}
            </label>
            <select
              id="station-select"
              value={cameraId}
              onChange={(e) => {
                setCameraId(e.target.value);
                setLastResult(null);
              }}
              style={{
                padding: "6px 10px",
                border: "1px solid var(--color-rule-2)",
                borderRadius: "var(--radius-pill)",
                maxWidth: "100%",
                background: "var(--paper)",
                fontFamily: "var(--font-dm-mono, monospace)",
                fontSize: "13px",
                fontWeight: 700,
              }}
            >
              {STATIONS.map((s) => (
                <option key={s.cameraId} value={s.cameraId}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
          <button
            type="button"
            className="tab"
            aria-pressed={isEn}
            onClick={() => setLang("en")}
          >
            English (EN)
          </button>
          <button
            type="button"
            className="tab"
            aria-pressed={!isEn}
            onClick={() => setLang("es")}
          >
            Español (ES)
          </button>
          <a
            href={`${API_PROXY}/worker/${encodeURIComponent(planId)}/${encodeURIComponent(cameraId)}/poster?lang=${lang}`}
            target="_blank"
            rel="noreferrer"
            className="btn ghost"
            style={{ padding: "6px 12px", fontSize: "12px" }}
          >
            {isEn ? "Printable QR Poster" : "Póster QR Imprimible"}
          </a>
        </div>
      </div>

      {/* Main 2-Column Mobile Receipt + Live Dispute Form */}
      <div
        style={{
          maxWidth: "880px",
          margin: "0 auto",
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(min(360px, 100%), 1fr))",
          gap: "var(--s4)",
          alignItems: "start",
        }}
      >
        {/* Left: Physical Paper Receipt Card */}
        <div
          className="root"
          style={{
            width: "100%",
            border: "1px solid var(--color-rule-2)",
            boxShadow: "none",
            background: "var(--paper)",
            paddingBottom: "var(--s3)",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "10px 16px",
              borderBottom: "1px solid var(--color-rule)",
              background: "var(--color-paper-3)",
              color: "var(--color-ink)",
            }}
          >
            <span className="dm" style={{ fontSize: "13px", fontWeight: 700 }}>
              {cameraId} · {page?.site_name?.toUpperCase() ?? "DEMO"}
            </span>
            <span className="dm" style={{ fontSize: "12px", color: "var(--color-accent)" }}>
              {isEn ? "ZERO IDENTITY LOGGED" : "CERO IDENTIDAD REGISTRADA"}
            </span>
          </div>

          <div style={{ padding: "18px 20px 12px" }}>
            <h1 className="h2" style={{ fontSize: "34px", lineHeight: 1.05 }}>
              {isEn
                ? `What ${cameraId} checks — in plain language.`
                : `Lo que revisa ${cameraId} — en lenguaje claro.`}
            </h1>
            {page?.rules.map((r) => {
              const cnt = page.counts[r.rule_id];
              return (
                <div
                  key={r.rule_id}
                  style={{
                    marginTop: "14px",
                    padding: "12px",
                    background: "var(--card)",
                    border: "1px solid var(--color-rule)",
                    borderRadius: "var(--radius-card)",
                  }}
                >
                  <div className="dm" style={{ fontSize: "12px", fontWeight: 700 }}>
                    {r.rule_id}
                  </div>
                  <p style={{ margin: "6px 0 10px", fontSize: "15px", lineHeight: 1.4 }}>
                    {r.plain_language ?? r.rule_id}
                  </p>
                  {cnt && (
                    <div
                      className="dm"
                      style={{
                        display: "flex",
                        gap: "12px",
                        fontSize: "12px",
                        borderTop: "1px dashed var(--color-rule-2)",
                        paddingTop: "8px",
                      }}
                    >
                      <span>
                        {isEn ? "Alerts:" : "Alertas:"} <b>{cnt.alerts}</b>
                      </span>
                      <span>
                        {isEn ? "Disputes:" : "Disputas:"} <b>{cnt.disputes}</b>
                      </span>
                      <span style={{ color: "var(--ok)" }}>
                        {isEn ? "Upheld:" : "Aceptadas:"} <b>{cnt.upheld}</b>
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Privacy Never-List Receipt */}
          <div
            style={{
              margin: "8px 16px 0",
              background: "var(--card)",
              color: "var(--color-ink)",
              border: "1px solid var(--color-rule)",
              borderRadius: "var(--radius-card)",
              padding: "18px 18px 12px",
              position: "relative",
            }}
          >
            <div className="tape" style={{ margin: "-18px -18px 14px", height: "10px" }} />
            <div className="dm" style={{ fontSize: "12px", fontWeight: 700, marginBottom: "8px" }}>
              {isEn
                ? `PRIVACY COVENANT · RETENTION ${page?.retention_days ?? 30} DAYS:`
                : `PACTO DE PRIVACIDAD · RETENCIÓN ${page?.retention_days ?? 30} DÍAS:`}
            </div>
            {(page?.never_list ?? ["faces", "names", "speed_scores", "emotion", "break_rooms"]).map(
              (item) => (
                <div key={item} className="no" style={{ fontSize: "13px", padding: "3px 0" }}>
                  <span style={{ fontWeight: 700, color: "var(--alert)" }}>×</span>
                  <span>
                    {STRINGS[lang].neverItems[item] ?? item}
                  </span>
                </div>
              ),
            )}
          </div>
        </div>

        {/* Right: Anonymous Event Codes & Live Dispute Filing */}
        <div
          style={{
            border: "1px solid var(--color-rule)",
            borderRadius: "var(--radius-card)",
            background: "var(--card)",
            padding: "22px",
          }}
        >
          <div className="dm" style={{ fontSize: "12px", fontWeight: 700, color: "var(--ink-2)" }}>
            {isEn
              ? "ANONYMOUS 15-MINUTE BUCKET RECEIPTS"
              : "RECIBOS ANÓNIMOS EN INTERVALOS DE 15 MINUTOS"}
          </div>
          <h2 className="h2" style={{ fontSize: "30px", marginTop: "4px", marginBottom: "12px" }}>
            {isEn ? "Challenge an Alert (Zero Login)" : "Impugnar una Alerta (Sin Inicio de Sesión)"}
          </h2>

          {errorMsg && (
            <div
              style={{
                padding: "10px 12px",
                background: "var(--color-bad-soft)",
                color: "var(--color-bad)",
                marginBottom: "12px",
                fontWeight: 600,
                fontSize: "14px",
              }}
            >
              {errorMsg}
            </div>
          )}

          {lastResult && (
            <div
              style={{
                margin: "0 0 16px",
                border: "3px solid var(--color-bad)",
                color: "var(--color-bad)",
                padding: "12px",
                background: "var(--color-paper-2)",
                textAlign: "center",
              }}
            >
              <div
                className="cd"
                style={{ fontWeight: 800, fontSize: "24px", lineHeight: 1 }}
              >
                {isEn
                  ? `DISPUTE #${lastResult.code} · ${lastResult.outcome.toUpperCase()}`
                  : `DISPUTA #${lastResult.code} · ${lastResult.outcome.toUpperCase()}`}
              </div>
              <div className="dm" style={{ fontSize: "12px", marginTop: "6px", color: "var(--color-ink)" }}>
                {isEn
                  ? `Chained to SHA-256 ledger · Rule ${lastResult.rule_id} now has ${lastResult.counts.disputes} dispute(s)`
                  : `Anclado a la cadena SHA-256 · Regla ${lastResult.rule_id} tiene ${lastResult.counts.disputes} disputa(s)`}
              </div>
            </div>
          )}

          {page && page.events.length > 0 ? (
            <form onSubmit={(e) => void handleSubmitDispute(e)}>
              <div style={{ marginBottom: "14px" }}>
                <label className="small" style={{ fontWeight: 700, display: "block", marginBottom: "6px" }}>
                  {isEn ? "1. Select 8-Digit Anonymous Event Code:" : "1. Código Anónimo del Evento:"}
                </label>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                  {page.events.map((ev) => (
                    <label
                      key={ev.code}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        padding: "10px 12px",
                        border:
                          selectedCode === ev.code
                            ? "1px solid var(--color-rule)"
                            : "1px solid rgba(20,20,20,0.3)",
                        background: selectedCode === ev.code ? "var(--yellow)" : "var(--paper)",
                        cursor: "pointer",
                      }}
                    >
                      <span className="dm" style={{ fontWeight: 700 }}>
                        <input
                          type="radio"
                          name="event_code"
                          value={ev.code}
                          checked={selectedCode === ev.code}
                          onChange={() => setSelectedCode(ev.code)}
                          style={{ marginRight: "8px" }}
                        />
                        CODE #{ev.code} · {ev.bucket_start.slice(11, 16)} UTC
                      </span>
                      <span className={`state ${ev.dispute === "upheld" ? "ok" : ""}`}>
                        {ev.dispute ? ev.dispute.toUpperCase() : "OPEN"}
                      </span>
                    </label>
                  ))}
                </div>
              </div>

              <div style={{ marginBottom: "14px" }}>
                <label className="small" style={{ fontWeight: 700, display: "block", marginBottom: "6px" }}>
                  {isEn ? "2. Why was this alert inaccurate?" : "2. ¿Por qué fue inexacta esta alerta?"}
                </label>
                <select
                  value={reasonChoice}
                  onChange={(e) => setReasonChoice(e.target.value as DisputeReason)}
                  style={{
                    width: "100%",
                    padding: "10px",
                    border: "1px solid var(--color-rule)",
                    borderRadius: "var(--radius-card)",
                    background: "var(--paper)",
                    fontSize: "14px",
                    fontWeight: 600,
                  }}
                >
                  {DISPUTE_REASONS.map((r) => (
                    <option key={r} value={r}>
                      {STRINGS[lang].reasons[r]}
                    </option>
                  ))}
                </select>
              </div>

              <div style={{ marginBottom: "16px" }}>
                <label className="small" style={{ fontWeight: 700, display: "block", marginBottom: "6px" }}>
                  {isEn
                    ? "3. Optional Note (Never tied to your name or IP):"
                    : "3. Nota opcional (Anónima):"}
                </label>
                <textarea
                  rows={3}
                  maxLength={500}
                  value={reasonText}
                  onChange={(e) => setReasonText(e.target.value)}
                  placeholder={
                    isEn
                      ? "e.g. I was seated inside the forklift cab, or overhead shadow hid my hardhat…"
                      : "ej. Estaba sentado dentro de la cabina del montacargas…"
                  }
                  style={{
                    width: "100%",
                    padding: "10px",
                    border: "1px solid var(--color-rule)",
                    borderRadius: "var(--radius-card)",
                    background: "var(--paper)",
                    fontFamily: "inherit",
                    fontSize: "14px",
                  }}
                />
              </div>

              <button
                type="submit"
                className="btn alert block"
                disabled={submitting || !selectedCode}
              >
                {submitting
                  ? isEn
                    ? "Submitting to Tier-2 Verifier & Chain…"
                    : "Enviando disputa…"
                  : isEn
                    ? `Submit Anonymous Dispute for #${selectedCode}`
                    : `Enviar Disputa Anónima para #${selectedCode}`}
              </button>
            </form>
          ) : (
            <div
              style={{
                padding: "16px",
                background: "var(--paper)",
                border: "1px solid var(--color-rule-2)",
                borderRadius: "var(--radius-pill)",
                maxWidth: "100%",
              }}
            >
              <p className="small">
                {isEn
                  ? `No recent alerts recorded on ${cameraId} in the last 15-minute window. Switch to CAM-02, CAM-03, CAM-04, CAM-05, CAM-06, CAM-08, or CAM-09 above to test disputing a live event.`
                  : `No hay alertas recientes en ${cameraId}. Seleccione otra cámara arriba.`}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
