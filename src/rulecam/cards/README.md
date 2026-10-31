# `rulecam.cards`

Rule cards and declined cards (task P2-10, MP §12), read from a saved plan
(`plans/<id>.plan.yaml`) and rendered to HTML (Jinja2, autoescaped) and to 1080 x 1350 PNGs
(Playwright, headless Chromium). Owner: P2.

## Public API

| Name | What it does |
|---|---|
| `load_plan_cards(store, plan_id, *, lang, base_url)` | Loads the saved plan (`PlanStore`, plan id validated) and builds its cards |
| `plan_cards(plan, *, lang, base_url)` | `(rule cards, declined cards)` in plan order |
| `render_rule_card_html(card, *, skeleton_png)` | Rule card HTML; `skeleton_png` is the P4-05 skeleton render (PNG bytes) |
| `render_declined_card_html(card)` | Declined card HTML |
| `render_card_png(html, path)` | PNG via Playwright (`uv sync --extra ui`); raises `CardError` without it |

## Where each field comes from

- **Plain language**: Glimmer's back-translation (P2-07) of the rule's IR in the crew language
  (the plan's first language unless `lang` is given). A `not back-translated` rule says so.
- **Cited standard**: `rule.source.standard`; "quote verified" from its `citation_text_match` check.
- **Feasibility**: the verdict of the concern whose `concern_covered` check names the rule.
- **Rule CI**: the rule's `ci_green` check (P2-08/09); its detail starts with
  `RuleCIReport.summary()` (`boundary 13/13, mutations 5/5; ...; 3 lookalikes, fooled 0`).
  No check: "Rule CI has not run". No lookalike report: "Lookalikes not run".
- **Calibrated false-alarm rate**: `rule.verify.calibrated_alpha`, only when set.
- **Checked by**: Glimmer when the rule was back-translated; Spark Open when the audit ran (an
  empty audit with a back-translation means no findings, since the audit hook writes both; an
  `audit_skipped:` finding means it did not).
- **QR code**: the worker page (P2-11) `<base_url>/worker/<plan_id>/<camera>?lang=`, only when a
  base URL is given.
- **Declined card**: category, reason and alternative only, never the request text. A curated
  (deterministic) decline is shown with its fixed policy text and category; a model decline's
  category is not recorded in the plan, and every request word that is not policy vocabulary is
  replaced by `[redacted]` in its reason and alternative.

## Tests

```bash
uv run pytest tests/cards                     # snapshots in tests/cards/snapshots/
RULECAM_UPDATE_SNAPSHOTS=1 uv run pytest tests/cards   # after an intended template change
uv run pytest tests/cards -m ui               # PNG size, needs Playwright + Chromium
```
