# Site Advisor sessions: arrival script and seal protocol

Records for the Site Advisor evaluation (E11) live here: sealed-cause hashes and reveals, the switchback pre-registration, the verdict and source labels. Design: `docs/site-advisor.md` §9 and §10. Task: P4-08a.

## Sessions

| Session | Planted cause | Purpose |
|---|---|---|
| Two sealed-cause sessions | yes, sealed before the session | Does the advisor find a cause it was never told? |
| One null session | none | A good advisor finds nothing |
| One live switchback | n/a | Six pairs of on/off blocks for the accepted recommendation |

All minutes are **set minutes** on the toy warehouse set. No real-world dollar figure comes from these sessions.

## Generate the operator card

```bash
uv run python scripts/arrivals.py --session S2 --seed 7 --duration-min 25 \
    --cause replenishment --cause-start-min 13 --cause-duration-min 6
```

- Arrivals are Poisson (`--rate-per-hour`, default 30) and fully determined by `--seed`.
- The cause never touches the random stream: a cause session and a null session with the same seed have identical truck lines.
- Use a **different seed per session**, and record seeds in the manifest.
- Give the card to the operator only. Do not show it to anyone who will analyse the session.

## Seal protocol

1. Print the canonical cause text: `uv run python scripts/arrivals.py --seed 7 --cause replenishment --print-cause`.
2. **Seal it before the session runs.** `rulecam advisor seal "<cause>"` (task P3-10) appends `sha256(cause + salt)` to the evidence chain and keeps the salt locally, offline.
3. Run the session from the card. Analyse it blind.
4. After the analysis is written down, reveal: publish the cause text and salt, and verify they match the sealed hash.
5. For the null session, seal the text `no planted cause` the same way.

> **Pending P3:** the seal CLI is task P3-10 and is not merged yet. How to seal in the meantime is waiting on P3's answer. No sealed-cause session runs until that is settled.

## What gets committed here

- Sealed hashes and the reveal for each session.
- The switchback pre-registration and its verdict.
- Source labels (`recorded`, `upload`, `public`, `simulated`) for every number.

Never commit raw video or the salts. Media lives in `data/`, which is git-ignored.

## Run the tests

```bash
uv run pytest tests/test_arrivals.py
```
