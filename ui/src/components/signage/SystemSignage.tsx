"use client";

import React from "react";

export function SystemSignage() {
  return (
    <div style={{ paddingBottom: "var(--s6)" }}>
      {/* Pagehead */}
      <section className="wrap pagehead">
        <div className="ctx">Rulecam design system · one stylesheet, <span className="dm">ds.css</span>, shared by every board</div>
        <h1 className="h1">Signage, not a dashboard.</h1>
      </section>

      <div className="tape" />

      {/* Colour Roles */}
      <section className="wrap sec">
        <h2 className="h3" style={{ marginBottom: "var(--s3)" }}>
          Colour: each one has a job
        </h2>
        <div className="grid" style={{ rowGap: "var(--s2)" }}>
          <div className="s3">
            <div style={{ height: "96px", display: "flex", alignItems: "flex-end", padding: "var(--s1) var(--s2)", fontFamily: "var(--font-dm-mono, monospace)", fontSize: "13px", border: "1px solid var(--ink)", background: "#ECEAE3", color: "#141414" }}>
              paper #ECEAE3
            </div>
            <p className="small" style={{ marginTop: "var(--s1)" }}>Ground for every screen a person reads.</p>
          </div>

          <div className="s3">
            <div style={{ height: "96px", display: "flex", alignItems: "flex-end", padding: "var(--s1) var(--s2)", fontFamily: "var(--font-dm-mono, monospace)", fontSize: "13px", border: "1px solid var(--ink)", background: "#141414", color: "#ECEAE3" }}>
              ink #141414
            </div>
            <p className="small" style={{ marginTop: "var(--s1)" }}>Text, rules, primary action.</p>
          </div>

          <div className="s3">
            <div style={{ height: "96px", display: "flex", alignItems: "flex-end", padding: "var(--s1) var(--s2)", fontFamily: "var(--font-dm-mono, monospace)", fontSize: "13px", border: "1px solid var(--ink)", background: "#F2D100", color: "#141414" }}>
              yellow #F2D100
            </div>
            <p className="small" style={{ marginTop: "var(--s1)" }}>Look here: highlight, progress, active step.</p>
          </div>

          <div className="s3">
            <div style={{ height: "96px", display: "flex", alignItems: "flex-end", padding: "var(--s1) var(--s2)", fontFamily: "var(--font-dm-mono, monospace)", fontSize: "13px", border: "1px solid var(--ink)", background: "#C93410", color: "#fff" }}>
              alert #C93410
            </div>
            <p className="small" style={{ marginTop: "var(--s1)" }}>A breach. Text on paper. On camera: #FF5A2B.</p>
          </div>

          <div className="s3">
            <div style={{ height: "96px", display: "flex", alignItems: "flex-end", padding: "var(--s1) var(--s2)", fontFamily: "var(--font-dm-mono, monospace)", fontSize: "13px", border: "1px solid var(--ink)", background: "#0B62A8", color: "#fff" }}>
              machine #0B62A8
            </div>
            <p className="small" style={{ marginTop: "var(--s1)" }}>What system perceived. On camera: #3DB8FF.</p>
          </div>

          <div className="s3">
            <div style={{ height: "96px", display: "flex", alignItems: "flex-end", padding: "var(--s1) var(--s2)", fontFamily: "var(--font-dm-mono, monospace)", fontSize: "13px", border: "1px solid var(--ink)", background: "#0F1112", color: "#ECEAE3" }}>
              camera #0F1112
            </div>
            <p className="small" style={{ marginTop: "var(--s1)" }}>Only behind footage and plans. Dark in both themes.</p>
          </div>

          <div className="s3">
            <div style={{ height: "96px", display: "flex", alignItems: "flex-end", padding: "var(--s1) var(--s2)", fontFamily: "var(--font-dm-mono, monospace)", fontSize: "13px", border: "1px solid var(--ink)", background: "#DEDBD2", color: "#141414" }}>
              paper 2 #DEDBD2
            </div>
            <p className="small" style={{ marginTop: "var(--s1)" }}>Code blocks and drawings on paper.</p>
          </div>

          <div className="s3">
            <div style={{ height: "96px", display: "flex", alignItems: "flex-end", padding: "var(--s1) var(--s2)", fontFamily: "var(--font-dm-mono, monospace)", fontSize: "13px", border: "1px solid var(--ink)", background: "#4A4A46", color: "#fff" }}>
              ink 2 #4A4A46
            </div>
            <p className="small" style={{ marginTop: "var(--s1)" }}>Secondary text. 7.6:1 on paper.</p>
          </div>
        </div>
      </section>

      {/* Typography voices */}
      <section className="wrap sec">
        <h2 className="h3" style={{ marginBottom: "var(--s3)" }}>
          Type: three purposeful voices
        </h2>
        <div style={{ borderTop: "2px solid var(--ink)" }}>
          <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: "var(--s2)", padding: "16px 0", borderBottom: "1px solid var(--ink)", alignItems: "baseline" }}>
            <span className="small">h1 · Barlow Condensed 700</span>
            <div className="h1" style={{ fontSize: "56px" }}>What happened, in a plain sentence</div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: "var(--s2)", padding: "16px 0", borderBottom: "1px solid var(--ink)", alignItems: "baseline" }}>
            <span className="small">h2 · Barlow Condensed 700</span>
            <div className="h2" style={{ fontSize: "38px" }}>Follow each word to what enforces it</div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: "var(--s2)", padding: "16px 0", borderBottom: "1px solid var(--ink)", alignItems: "baseline" }}>
            <span className="small">h3 · Barlow Condensed 700</span>
            <div className="h3">Two zones, one order</div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: "var(--s2)", padding: "16px 0", borderBottom: "1px solid var(--ink)", alignItems: "baseline" }}>
            <span className="small">num-xl / l / m</span>
            <div style={{ display: "flex", gap: "var(--s4)", alignItems: "baseline" }}>
              <span className="num-xl" style={{ fontSize: "72px" }}>1.1 m</span>
              <span className="num-l" style={{ fontSize: "48px" }}>142</span>
              <span className="num-m">8.4 ms</span>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: "var(--s2)", padding: "16px 0", borderBottom: "1px solid var(--ink)", alignItems: "baseline" }}>
            <span className="small">lead · Barlow 20</span>
            <p className="lead">Used for the sentence under a heading and for direct answers.</p>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: "var(--s2)", padding: "16px 0", borderBottom: "1px solid var(--ink)", alignItems: "baseline" }}>
            <span className="small">mono · DM Mono 13</span>
            <p className="mono">sha256 9f2e7c41…01c4 · timecodes, cryptographic hashes, rule syntax only</p>
          </div>
        </div>
      </section>
    </div>
  );
}
