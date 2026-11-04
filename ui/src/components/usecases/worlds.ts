// The three worlds one engine is pointed at. Each line is said in the project's own facts
// (SUBMISSION.md's model map, the README's site tools); no figure is claimed here.

export const WORLDS = [
  {
    slug: "supermarket",
    name: "Supermarket",
    promise: "Ask your store anything",
    line: "Ask what happened on the floor in plain words. Muse Spark answers from the event log and the video; SAM 3.1 confirms a shelf gap holds no product.",
    models: ["Muse Spark 1.3", "SAM 3.1"],
    icon: "cart",
  },
  {
    slug: "warehouse",
    name: "Warehouse",
    promise: "Forklift rules that prove themselves",
    line: "SAM 3.1 measures the gap between a person and a moving forklift, Rule CI attacks the rule, and Glimmer checks every alert on the frames.",
    models: ["SAM 3.1", "Muse Glimmer 30B"],
    icon: "forklift",
  },
  {
    slug: "crowd",
    name: "Crowd",
    promise: "Crowd-crush early warning",
    line: "SAM 3.1 turns person tracks into people per square metre for each zone; Muse Spark watches the clip of every crowded spell.",
    models: ["SAM 3.1", "Muse Spark 1.2"],
    icon: "crowd",
  },
] as const;

export type World = (typeof WORLDS)[number];
export type WorldSlug = World["slug"];
