# Rulecam kit

Small, honest building blocks for every screen. Import from `@/components/kit`. They draw only
with the tokens in `app/globals.css` (Console: Host Grotesk for what a person reads, Atkinson
Hyperlegible Mono for what an instrument measured; amber means a person or a deviation, cool blue
an instrument, green and red status only), so they follow light and dark on their own.

Rules every caller keeps: never pass an invented figure (pass `null` and the kit says unknown);
always say where data comes from with `LiveBadge`; never present sample or replayed data as live.

| Component | Props | Use it for |
|---|---|---|
| `LiveBadge` | `mode`: `"live" \| "replay" \| "sample" \| "offline"`; `detail?`: short visible note ("recorded 1 Nov") | The provenance of any data on screen. Unknown is `offline`, never `live`. |
| `VerdictPill` | `verdict`: `"yes" \| "no" \| "unsure"` or Glimmer's `"violation" \| "no_violation" \| "uncertain"` (or a boolean); `confidence?`: 0..1 (Glimmer's P(violation)); `model?` (default `"Glimmer"`); `scoreLabel?` | A verifier's answer: model, words and score, never colour alone. `normaliseVerdict()` is exported. |
| `ModelChip` | `model`; `job?` ("measured the gap"); `ms?` (call time, shown as `640 ms` / `1.2 s`); `state?`: `"ran" \| "running" \| "skipped" \| "off"`; `meta?` (false greys a non-Meta part) | Which Meta model did this step and how long it took. `formatMs()` is exported. |
| `StepFlow` | `steps`: `{ name, text?, models?: (string \| ModelChipProps)[], state?: "done" \| "current" \| "todo" \| "failed", href? }[]`; `current?` (index); `label?` (list name); `orientation?`: `"row" \| "column"`; `size?`: `"default" \| "compact"`; `as?`: heading element (`"h3"` default); `columns?` (steps per row, e.g. 4 for a 7-step journey) | The journey: Describe, Plan, Prove, Trust. One column on a phone. |
| `EvidenceCard` | `frames`: `(url \| { src, alt? })[]`; `hash?`; `hashLabel?` ("Record hash"); `verifyHref?`; `title?`; `caption?`; `facts?`: `{ label, value }[]`; `emptyText?` | A frame strip pinned by its hash, with Copy hash and Verify. Says so when no frames are published. Client component. |
| `MetricTile` | `label`; `value` (`null` = unknown, said in words); `unit?`; `note?`; `source?`: `{ href, text }`; `tone?`: `"default" \| "ok" \| "bad" \| "cool" \| "accent"`; `size?`: `"md" \| "lg"`; `loading?` | One figure in the measured face. Anything quoted as a result carries its `source`. |
| `Timeline` + `TimelineItem` | `Timeline`: `label?`, children. `TimelineItem`: `time` (ISO, shown HH:MM:SS UTC, or any label like "+30 s"); `title`; `meta?`; `tone?`: `"person" \| "instrument" \| "ok" \| "bad" \| "neutral"`; children | An audit trail or escalation on one spine: a person in amber, a model in cool. |
| `AlertCard` | `title`; `rule?`; `camera?`; `time?`; `severity?`; `verdict?`: `VerdictPillProps`; `mode?`: `LiveMode`; `facts?`; children (body, e.g. an `EvidenceCard`); `actions?` (buttons) | One alert: what fired, where, the verifier's verdict and what to do. |
| `ZoneDensityBar` | `zones`: `{ id?, name, density: number \| null }[]`; `unit?` (default `"people/m²"`); `limit?`; `warnAt?`; `max?`; `title?`; `aside?` (e.g. a `LiveBadge`) | Crowd density per zone against the site's own limit. No limit given, no colour judgement. |
| `EmptyState` | `title`; children (why, and what fills it); `action?`; `icon?` (a name from `components/icons`); `tone?`: `"empty" \| "error"` | The empty and the error state of any region that loads data. `error` is announced. |
| `Skeleton` | `lines?`; `shape?`: `"text" \| "block" \| "circle"`; `height?` ("160px"); `label?` ("Loading alerts") | The loading state, in the shape of what is coming. Stops breathing under reduced motion. |

A region that loads data draws four states: `Skeleton` while loading, `EmptyState` when empty,
`EmptyState tone="error"` when the call failed (say what to do), and the content when full.

Icons live in `components/icons.tsx` (`icons.cart`, `icons.forklift`, `icons.crowd`, …), all
decorative; put the meaning in text or an `aria-label`.

Tests: `src/components/kit/kit.test.tsx`.
