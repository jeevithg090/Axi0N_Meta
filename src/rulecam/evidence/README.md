# `rulecam.evidence`: hash-chained event log (P3-07a)

## Purpose

Every candidate, verdict, action, ack, dispute, plan, advice and tamper attempt is appended to an
append-only JSONL chain, one file per site and UTC day (`<root>/<site>/<YYYY-MM-DD>.jsonl`).
Editing, deleting or reordering any record breaks the chain at that record (MP §0, §7.4).

Each line is an `EventRecord` (contracts §4.4) in canonical JSON: sorted keys, no whitespace,
UTF-8, UTC timestamps (`…Z`), and numbers written exactly as JavaScript `JSON.stringify` writes them,
so the browser verify page (P3-07b) recomputes the same hashes.

```
hash = sha256(prev_hash + canonical(record without "hash"))   # genesis prev_hash = "0" * 64, seq starts at 1
```

## Public API

```python
from rulecam.evidence import EventChain, verify_chain

chain = EventChain(settings.rulecam_data_dir / "evidence" / "chain", "main-st-kitchen", clock=clock)
record = chain.append("verdict", verdict)  # a contract model or a JSON mapping
report = verify_chain(chain.path_for(day))  # ChainReport(ok, first_bad_seq, n, reason)
```

Writers hold an exclusive `flock` on `<file>.lock` (POSIX), so concurrent appenders stay linked.
Payloads must be JSON (no NaN or infinity); integers above 2**53 lose precision in the browser.

## Tests

```bash
uv run pytest tests/evidence
```
