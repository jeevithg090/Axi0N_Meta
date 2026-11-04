# `rulecam.compliance`: the proof portal

Proof packs for inspectors, insurers, head office and crew representatives, built from the
hash-chained evidence log (`rulecam.evidence`). The full description, API contract and demo
steps are in [docs/usecases/proof-portal.md](../../../docs/usecases/proof-portal.md).

| Module | What it does |
|---|---|
| `engine.py` | Each number's recipe (`MetricSpec`) and its evaluation over chain records; mirrored in `ui/src/app/inspector/_lib/engine.ts` |
| `proofs.py` | Merkle inclusion proofs to a day's root, and the pack hash |
| `build.py` | Reads and verifies chain days, chooses the disclosed records, computes every metric, incidents, stills and notes |
| `rules.py` | Rules in force from the chained plan approvals and plan files; each rule's clause, grounded by Muse Spark 1.3 and checked on the cited page |
| `narrate.py` | Audience summaries with `{metric_id}` placeholders, filled from the pack |
| `share.py` | Pack store, signed expiring share tokens, revocation |
| `render.py`, `templates/proof.html.j2` | The printable pack (PDF through Playwright) |
| `api.py` | The `/proof` routes, mounted by `api.app.services_from_settings` |

```bash
uv run pytest tests/compliance                 # Python side, and the UI fixture check
cd ui && npx vitest run src/app/inspector      # the browser's checks on the same fixture
UPDATE_PROOF_FIXTURE=1 uv run pytest tests/compliance/test_pack.py   # rebuild the fixture
```
