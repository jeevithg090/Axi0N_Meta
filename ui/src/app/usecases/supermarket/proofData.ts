// The supermarket proof, copied from the 4 Nov live report (REPORT below). proof.test.tsx reads
// the report and fails if any of these values is not in it. Nothing here is typed from memory.

export const REPORT = "reports/eval/2026-11-04-supermarket-live.md";

/** The over-count Ask made before the cross-count tool, and what it says now, on the same frames. */
export const CROSS_COUNT = {
  camera: "checkout queue",
  frames: [
    { at: "01:13:07", byHand: "~12", tracker: "22", sam: "15-17", spark: "9", glimmer: "10-11", askNow: "10" },
    { at: "01:03:10", byHand: "~13", tracker: "21", sam: "15-16", spark: "9-10", glimmer: "11-13", askNow: "11" },
  ],
  reconciled: "SAM found 15 boxes but 9 had no person point, indicating duplicates",
  error: "Ask's error went from +10 and +8 to -2 and -2",
} as const;

/** The spill task: seen, staff alerted, cleared and checked by photo (the photo is a SAMPLE edit). */
export const HAZARD = {
  seen: "01:18:44",
  alerted: "01:18:44",
  cleared: "01:20:39",
  minutes: "1.9 min",
  sparkProof: "9.8 s",
  glimmerProof: "15.1 s",
  detectRecord: "#19",
  clearedRecord: "#21",
  chainRecords: "28 records",
} as const;
