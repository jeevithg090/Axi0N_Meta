// The three worlds one engine is pointed at. Each line is said in the project's own facts
// (SUBMISSION.md's model map, the README's site tools); no figure is claimed here.
// Each still is one frame of footage Rulecam actually ran on, exported in black and white
// (ui/public/usecases, 1200×750), and its credit says where it came from.

export const WORLDS = [
  {
    slug: "supermarket",
    name: "Supermarket",
    promise: "Ask your store anything",
    line: "Ask what happened on the floor in plain words. Muse Spark answers from the event log and the video; SAM 3.1 confirms a shelf gap holds no product.",
    models: ["Muse Spark 1.3", "SAM 3.1"],
    icon: "cart",
    still: {
      src: "/usecases/supermarket.jpg",
      label: "Stock footage, Pexels 39221979",
      alt: "Shoppers unloading trolleys at a supermarket checkout lane",
      credit: "Stock footage, Pexels video 39221979 (faces blurred): the checkout clip the Ask agent watched on 1 Nov.",
    },
  },
  {
    slug: "warehouse",
    name: "Warehouse",
    promise: "Forklift rules that prove themselves",
    line: "SAM 3.1 measures the gap between a person and a moving forklift, Rule CI attacks the rule, and Glimmer checks every alert on the frames.",
    models: ["SAM 3.1", "Muse Glimmer 30B"],
    icon: "forklift",
    still: {
      src: "/usecases/warehouse.jpg",
      label: "Stock footage, Pexels 32243654",
      alt: "A worker walks past a moving forklift in a warehouse aisle",
      credit: "Stock footage, Pexels video 32243654: the clip behind the 1 Nov verified alert (T02).",
    },
  },
  {
    slug: "crowd",
    name: "Crowd",
    promise: "Crowd-crush early warning",
    line: "SAM 3.1 turns person tracks into people per square metre for each zone; Muse Spark watches the clip of every crowded spell.",
    models: ["SAM 3.1", "Muse Spark 1.2"],
    icon: "crowd",
    still: {
      src: "/usecases/crowd.jpg",
      label: "Research footage, Jülich",
      alt: "Seen from above, a dense crowd in caps presses toward a narrow gate",
      credit: "Research footage, Jülich bottleneck experiment (run 030, Forschungszentrum Jülich data archive). Not a real venue.",
    },
  },
] as const;

export type World = (typeof WORLDS)[number];
export type WorldSlug = World["slug"];
