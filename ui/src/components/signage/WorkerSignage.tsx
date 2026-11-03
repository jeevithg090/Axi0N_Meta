"use client";

import React, { useState } from "react";

export function WorkerSignage() {
  const [lang, setLang] = useState<"en" | "es">("en");
  const [filed, setFiled] = useState<boolean>(false);
  const [recording, setRecording] = useState<boolean>(false);

  const isEn = lang === "en";

  return (
    <div style={{ display: "flex", justifyContent: "center", padding: "var(--s4) var(--s2) var(--s6)" }}>
      {/* 390px Mobile Viewport Container */}
      <div
        className="root"
        style={{
          width: "100%",
          maxWidth: "410px",
          border: "4px solid var(--ink)",
          boxShadow: "0 12px 32px rgba(0,0,0,0.15)",
          paddingBottom: "var(--s4)",
        }}
      >
        {/* Top Header / Language */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "10px 16px 0",
            borderBottom: "1px solid var(--ink)",
          }}
        >
          <span className="dm small" style={{ fontSize: "13px" }}>
            Station 4 · Anon. #882
          </span>
          <div>
            <button
              type="button"
              className="tab"
              aria-pressed={isEn}
              onClick={() => setLang("en")}
            >
              EN
            </button>
            <button
              type="button"
              className="tab"
              aria-pressed={!isEn}
              onClick={() => setLang("es")}
            >
              ES
            </button>
          </div>
        </div>

        {/* Headline */}
        <div style={{ padding: "18px 20px 24px" }}>
          <h1 className="h1" style={{ fontSize: "52px" }}>
            {isEn ? "Washed for 14.2 s. Rule requires 20." : "Lavó 14.2 s. La norma pide 20."}
          </h1>
          <p className="lead" style={{ marginTop: "12px", fontSize: "17px" }}>
            {isEn
              ? "The prep slicer stayed locked for 5.8 seconds until hygiene criteria were met."
              : "La rebanadora permaneció bloqueada durante 5.8 segundos hasta cumplir la higiene."}
          </p>
        </div>

        {/* Physical Paper Receipt */}
        <div
          style={{
            margin: "0 14px",
            background: "var(--card, #FBFAF6)",
            color: "#141414",
            border: "2px solid var(--ink)",
            padding: "24px 20px 8px",
            position: "relative",
          }}
        >
          <div className="tape" style={{ margin: "-24px -20px 18px", height: "12px" }} />

          <div className="dm" style={{ fontSize: "13px", letterSpacing: ".02em", textAlign: "center" }}>
            {isEn ? "RECEIPT · HANDWASHING SINK A" : "RECIBO · FREGADERO DE MANOS A"}
          </div>
          <div className="dm" style={{ fontSize: "12px", textAlign: "center", color: "#4A4A46", marginTop: "2px" }}>
            06:42 · episode CK-012 · SHA256 9a2f…881c
          </div>

          <div style={{ borderTop: "2px dashed #141414", margin: "16px 0 10px" }} />

          <div className="li">
            <span>{isEn ? "Time measured at sink" : "Tiempo en fregadero"}</span>
            <i />
            <b>14.2 s</b>
          </div>
          <div className="li">
            <span>{isEn ? "Required by FDA code" : "Requerido por norma"}</span>
            <i />
            <b>20.0 s</b>
          </div>
          <div className="li" style={{ fontSize: "18px", paddingTop: "8px" }}>
            <span style={{ fontWeight: 600 }}>{isEn ? "Deficit" : "Déficit"}</span>
            <i />
            <b style={{ color: "var(--alert, #C93410)" }}>5.8 s</b>
          </div>

          {/* Graphical Duration Meter */}
          <svg
            viewBox="0 0 346 30"
            style={{ display: "block", width: "100%", margin: "10px 0 4px" }}
            role="img"
            aria-label="14.2 of 20 seconds meter"
          >
            <rect y="8" width="346" height="14" fill="#DCD8CC" />
            <rect y="8" width="246" height="14" fill="#141414" />
            <rect x="246" y="8" width="100" height="14" fill="#F2D100" />
          </svg>

          <div className="li">
            <span>{isEn ? "Slicer interlock trigger" : "Bloqueo cortadora"}</span>
            <i />
            <b>{isEn ? "Power cut (8 ms)" : "Corte (8 ms)"}</b>
          </div>

          <div style={{ borderTop: "2px dashed #141414", margin: "14px 0 10px" }} />

          <div className="dm" style={{ fontSize: "13px", color: "#4A4A46", marginBottom: "6px" }}>
            {isEn ? "Privacy guarantees for this event:" : "Garantías de privacidad del evento:"}
          </div>
          <div className="no">
            <span style={{ fontWeight: 700 }}>×</span>
            <span>{isEn ? "No facial geometry stored" : "Sin geometría facial almacenada"}</span>
          </div>
          <div className="no">
            <span style={{ fontWeight: 700 }}>×</span>
            <span>{isEn ? "No raw voice retained" : "Sin grabaciones de voz retenidas"}</span>
          </div>
          <div className="no">
            <span style={{ fontWeight: 700 }}>×</span>
            <span>{isEn ? "No payroll or identity cross-reference" : "Sin enlace con nómina o identidad"}</span>
          </div>
          <div className="no">
            <span style={{ fontWeight: 700 }}>○</span>
            <span>{isEn ? "Cryptographic receipt stored locally" : "Recibo criptográfico guardado local"}</span>
          </div>

          {/* Red Rubber Dispute Stamp */}
          {filed && (
            <div
              style={{
                margin: "18px 0 6px",
                border: "3px solid #C93410",
                color: "#C93410",
                padding: "10px 12px",
                transform: "rotate(-3deg)",
                textAlign: "center",
              }}
            >
              <div
                className="cd"
                style={{ fontWeight: 800, fontSize: "28px", lineHeight: 1, textTransform: "uppercase" }}
              >
                {isEn ? "DISPUTED · PENDING REVIEW" : "DISPUTADO · EN REVISIÓN"}
              </div>
              <div className="dm" style={{ fontSize: "12px", marginTop: "4px" }}>
                {isEn ? "Receipt entered into Rule CI regression ledger" : "Ingresado al registro de regresión"}
              </div>
            </div>
          )}

          {/* Serrated Bottom Edge */}
          <svg
            viewBox="0 0 346 14"
            preserveAspectRatio="none"
            style={{
              display: "block",
              width: "calc(100% + 40px)",
              maxWidth: "none",
              margin: "18px -20px -2px",
              height: "14px",
            }}
            aria-hidden="true"
          >
            <path
              d="M0 0H346V14L335 4 324 14 313 4 302 14 291 4 280 14 269 4 258 14 247 4 236 14 225 4 214 14 203 4 192 14 181 4 170 14 159 4 148 14 137 4 126 14 115 4 104 14 93 4 82 14 71 4 60 14 49 4 38 14 27 4 16 14 5 4 0 14Z"
              fill="var(--paper)"
            />
          </svg>
        </div>

        {/* Worker Actions */}
        <div style={{ padding: "20px 16px 0", display: "flex", flexDirection: "column", gap: "12px" }}>
          <button
            type="button"
            className={`btn block ${filed ? "o" : "alert"}`}
            onClick={() => setFiled(!filed)}
          >
            {filed ? "✓ Dispute Filed" : isEn ? "File a dispute on this event" : "Presentar una disputa"}
          </button>

          <button
            type="button"
            className="btn o block"
            onClick={() => setRecording(!recording)}
          >
            {recording
              ? "🔴 Recording audio note..."
              : isEn
              ? "🎙 Record Spanish / English voice dispute"
              : "🎙 Grabar nota de voz en español"}
          </button>
        </div>
      </div>
    </div>
  );
}
