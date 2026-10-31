"""Jinja2 HTML templates for Rule Cards and Declined Cards (task P2-10, 1080x1350)."""

from __future__ import annotations

RULE_CARD_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ card.language }}">
<head>
  <meta charset="UTF-8">
  <title>Rule Card: {{ card.rule_id }}</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      width: 1080px;
      height: 1350px;
      background: #0f172a;
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      padding: 64px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 2px solid #334155;
      padding-bottom: 24px;
    }
    .brand {
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -0.5px;
      color: #38bdf8;
    }
    .meta-pills {
      display: flex;
      gap: 12px;
    }
    .pill {
      font-size: 18px;
      font-weight: 600;
      padding: 8px 16px;
      border-radius: 9999px;
      background: #1e293b;
      color: #94a3b8;
    }
    .verdict-green { background: #064e3b; color: #34d399; border: 1px solid #059669; }
    .verdict-amber { background: #78350f; color: #fbbf24; border: 1px solid #d97706; }
    .verdict-red { background: #7f1d1d; color: #f87171; border: 1px solid #dc2626; }

    .body {
      flex: 1;
      padding: 48px 0;
      display: flex;
      flex-direction: column;
      gap: 36px;
    }
    .rule-title {
      font-size: 48px;
      font-weight: 700;
      line-height: 1.25;
      color: #ffffff;
    }
    .standard-box {
      background: #1e293b;
      padding: 24px 32px;
      border-radius: 16px;
      border-left: 6px solid #38bdf8;
    }
    .standard-label {
      font-size: 16px;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: #94a3b8;
      margin-bottom: 8px;
    }
    .standard-text {
      font-size: 24px;
      font-weight: 600;
      color: #e2e8f0;
    }

    .ci-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 20px;
    }
    .ci-card {
      background: #1e293b;
      padding: 24px;
      border-radius: 16px;
      text-align: center;
      border: 1px solid #334155;
    }
    .ci-val {
      font-size: 44px;
      font-weight: 800;
      color: #10b981;
      margin-bottom: 6px;
    }
    .ci-label {
      font-size: 16px;
      color: #94a3b8;
      font-weight: 500;
    }

    .thumbnail-box {
      height: 260px;
      background: #090d16;
      border: 2px dashed #334155;
      border-radius: 16px;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }
    .thumbnail-box img {
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
    }
    .thumbnail-placeholder {
      color: #64748b;
      font-size: 20px;
      font-weight: 500;
    }

    .footer {
      border-top: 2px solid #334155;
      padding-top: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .verified-by {
      font-size: 20px;
      color: #94a3b8;
      font-weight: 600;
    }
    .verified-badge {
      color: #38bdf8;
    }
    .qr-text {
      font-size: 16px;
      color: #64748b;
      text-align: right;
    }
  </style>
</head>
<body>
  <div class="header">
    <div class="brand">Rulecam</div>
    <div class="meta-pills">
      <span class="pill">{{ card.site_id }} · {{ card.camera_id }}</span>
      <span class="pill verdict-{{ card.feasibility_verdict }}">
        Feasibility: {{ card.feasibility_verdict|upper }}
      </span>
      <span class="pill">{{ card.language|upper }}</span>
    </div>
  </div>

  <div class="body">
    <h1 class="rule-title">{{ card.plain_language }}</h1>

    <div class="standard-box">
      <div class="standard-label">Cited Regulatory Standard</div>
      <div class="standard-text">{{ card.cited_standard }}</div>
    </div>

    <div class="ci-grid">
      <div class="ci-card">
        <div class="ci-val">{{ card.boundary_traces_passed }}/{{ card.boundary_traces_total }}</div>
        <div class="ci-label">Boundary Traces</div>
      </div>
      <div class="ci-card">
        <div class="ci-val">{{ card.mutants_killed }}/{{ card.mutants_total }}</div>
        <div class="ci-label">Mutants Killed</div>
      </div>
      <div class="ci-card">
        <div class="ci-val">{{ card.lookalikes_fooled }}/{{ card.lookalikes_tested }}</div>
        <div class="ci-label">Lookalikes Fooled</div>
      </div>
    </div>

    <div class="thumbnail-box">
      {% if card.skeleton_thumbnail_b64 %}
        <img src="data:image/png;base64,{{ card.skeleton_thumbnail_b64 }}" alt="Skeleton Thumbnail">
      {% else %}
        <div class="thumbnail-placeholder">Skeleton Thumbnail (Verified Detection Region)</div>
      {% endif %}
    </div>
  </div>

  <div class="footer">
    <div class="verified-by">
      Checked by <span class="verified-badge">{{ card.verified_by }}</span>
      {% if card.calibrated_false_alarm_rate is not none %}
        · FAR: {{ "%.3f"|format(card.calibrated_false_alarm_rate) }}/shift
      {% endif %}
    </div>
    <div class="qr-text">
      Scan to view hash-chained evidence<br>
      <code>{{ card.qr_code_url }}</code>
    </div>
  </div>
</body>
</html>
"""

DECLINED_CARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Declined Request Card</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      width: 1080px;
      height: 1350px;
      background: #0f172a;
      color: #f8fafc;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      padding: 64px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 2px solid #334155;
      padding-bottom: 24px;
    }
    .brand {
      font-size: 28px;
      font-weight: 800;
      color: #94a3b8;
    }
    .declined-badge {
      font-size: 20px;
      font-weight: 800;
      padding: 10px 24px;
      border-radius: 9999px;
      background: #7f1d1d;
      color: #fca5a5;
      border: 1px solid #ef4444;
      text-transform: uppercase;
      letter-spacing: 1px;
    }

    .body {
      flex: 1;
      padding: 64px 0;
      display: flex;
      flex-direction: column;
      gap: 40px;
    }
    .category-title {
      font-size: 52px;
      font-weight: 800;
      color: #ffffff;
      line-height: 1.2;
    }
    .category-pill {
      display: inline-block;
      font-size: 22px;
      font-weight: 700;
      padding: 8px 20px;
      border-radius: 8px;
      background: #334155;
      color: #f87171;
      margin-bottom: 8px;
    }

    .section-box {
      background: #1e293b;
      padding: 32px;
      border-radius: 16px;
      border-left: 6px solid #ef4444;
    }
    .alt-box {
      background: #1e293b;
      padding: 32px;
      border-radius: 16px;
      border-left: 6px solid #10b981;
    }
    .section-label {
      font-size: 18px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-bottom: 12px;
    }
    .reason-label { color: #f87171; }
    .alt-label { color: #34d399; }

    .section-text {
      font-size: 26px;
      line-height: 1.45;
      color: #e2e8f0;
      font-weight: 500;
    }

    .guarantee-box {
      background: #090d16;
      border: 1px solid #334155;
      border-radius: 16px;
      padding: 24px 32px;
      font-size: 20px;
      color: #94a3b8;
      line-height: 1.4;
    }
    .guarantee-highlight {
      color: #38bdf8;
      font-weight: 600;
    }

    .footer {
      border-top: 2px solid #334155;
      padding-top: 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 18px;
      color: #64748b;
    }
  </style>
</head>
<body>
  <div class="header">
    <div class="brand">Rulecam Ethics & Governance</div>
    <div class="declined-badge">Request Declined</div>
  </div>

  <div class="body">
    <div>
      <div class="category-pill">{{ card.category }}</div>
      <h1 class="category-title">Safety and Privacy Boundary Enforced</h1>
    </div>

    <div class="section-box">
      <div class="section-label reason-label">Why this was declined</div>
      <div class="section-text">{{ card.reason }}</div>
    </div>

    <div class="alt-box">
      <div class="section-label alt-label">Compliant Station Alternative</div>
      <div class="section-text">{{ card.station_alternative }}</div>
    </div>

    <div class="guarantee-box">
      <span class="guarantee-highlight">Core Design Principle:</span>
      Rulecam monitors stations, equipment, and workflows. Individual worker tracking,
      per-person speed quotas, and biometric recognition are architecturally prohibited.
    </div>
  </div>

  <div class="footer">
    <div>{{ card.site_id }}</div>
    <div>{{ card.policy_reference }}</div>
  </div>
</body>
</html>
"""
