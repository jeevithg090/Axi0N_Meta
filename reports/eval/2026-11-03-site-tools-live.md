# Site tools for the festival and supermarket demos: live runs (3 Nov 2026)

Three tools that pair Meta Model API services the product did not use yet with the two demo use
cases. Everything below ran live against `api.meta.ai` from a laptop (WSL, `RULECAM_MODE=live`)
on 3 Nov 2026, not on the deployed stack. **The shelf image and the crowd clip were generated
with Muse Image for these tests**, because no shelf or festival footage is recorded yet. They
show that the pipeline works end to end, not how accurate it is on real cameras.

| Tool | Meta services | What is checked, by what |
|---|---|---|
| `/context` site context | Muse Spark 1.3 + `web_search` (`user_location`, `include: [web_search_call.results]`) | Each item's source must be a page the search returned; otherwise one relink round picks it from the numbered retrieved pages, or the item is dropped |
| `/shelf` shelf gaps | Muse Spark 1.3 image understanding + SAM 3.1 (`product package`) | A gap Spark proposes counts only when SAM's products cover at most 25% of it |
| `/crowd` crowd density | SAM 3.1 person tracks (track store) + Muse Spark 1.3 video understanding (`input_video`) | Density alone can escalate; the video check can only add reasons (surge, person down, pushing at a barrier, blocked exit), and flags spells it does not see as dense |

## 1. Site context (`rulecam context`)

**Festival** (Glastonbury, GB; notes "Glastonbury Festival site, Worthy Farm"). Run 2: 11 searches,
73 pages read, **3 items kept, 0 dropped**: the Met Office 3-day forecast (gusts to 26 mph on
Wednesday), "no Met Office weather warnings", and that 2026 is a fallow year with no running
order. All three link to pages the search returned, and all three were also cited. Run 1 had
dropped "No Met Office weather warnings" because its source URL (the warnings index) was not a
returned page. That is the check doing its job.

**Supermarket** (Manchester, GB). Run 1, before the relink round: 11 searches, 89 pages, **0 of 5
items kept**. Spark named site home pages (`alerts.food.gov.uk`, `manchester.gov.uk`,
`visitmanchester.com`) instead of the pages it had read. Run 2, with relink: 15 searches, 87
pages, **5 kept, 1 dropped**:

- two Food Standards Agency recalls with their exact alert pages (Walkers Variety Snacks Sharing
  Box, batch 107543; Gilbert's Chopped Liver), each with "check shelves and remove affected
  packs";
- two Bonfire Night events (4 and 5 Nov), for evening footfall;
- schools back after half-term (council term-dates page);
- dropped: a Manchester forecast whose page no search returned, *"and no returned page says it"*.

## 2. Shelf gaps (`rulecam shelf-scan`)

The test image (1920x1280) is a generated aisle with cereal and pasta bays, where the centre-left
bays have 5 empty shelf stretches. SAM 3.1 found **zero** matches for "empty shelf space" or "gap on
shelf", but 74 to 83 matches for "product package" and "grocery product". So Spark proposes the
gaps and SAM confirms they hold no product.

| Run | Spark boxes | Kept | Rejected (SAM product cover) |
|---|---|---|---|
| pixel coordinates, run 1 | 5 | 3 | 2 (78%, 89%: boxes over cereal rows) |
| pixel coordinates, run 2 | 6 | 4 | 2 (91%, 55%); boxes squeezed into the left half, centre bay missed |
| 0–1000 grid, run 1 | 6 | **5** | 1 (100%) |
| 0–1000 grid, run 2 | 7 | **5** | 2 (99%, 54%) |

With pixel coordinates Spark's boxes moved between runs. On a 0–1000 grid, both runs gave the
same 5 gaps, matching the 5 empty stretches, so the code uses the grid. Every rejection sat on
products.

## 3. Crowd density (`/crowd/scan`, smoke test)

Synthetic tracks (18 people about 0.35 m apart for 12 s) gave a `crowded` spell, peak **4.8
people per m²**. The clip was 8 s: two Muse Image frames of a festival crowd at the front
barrier, the second with the crowd surging and one person down. Spark 1.3 watched the MP4 and
returned `dense: true` and `movement: surging`, plus two signs: `person_down` and
`pushing_at_barrier`, **both at 4.0 s, the first frame of the second image**. The spell escalated
for three reasons: person down, pushing at barrier, crowd surging.

## Not done yet

- No real festival or shelf footage: accuracy on real cameras is unmeasured.
- Crowd density needs a calibrated camera (metric foot points). SAM undercounts dense crowds, so
  densities are lower bounds, and each report says so.
- The tools are in the app (owner-only routes and the Site tools page) but not on the deployed
  RunPod stack.
