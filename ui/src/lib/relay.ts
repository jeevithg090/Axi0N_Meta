// Relay HUD logic: which models actually ran in the current pipeline segment.
// Only models that ran appear; non-Meta parts are shown grey; the list resets when the segment changes.

import type { ModelUsed } from "./events";

const META_PREFIXES = [
  "sam",
  "muse",
  "meta-models/",
  "glimmer",
  "spark",
  "mms-tts",
  "seamless",
  "sapiens",
  "v-jepa",
  "vjepa",
  "prompt-guard",
  "llama",
  "dinov3",
  "video-seal",
  "pe-a-frame",
];

export interface HudModel {
  model: string;
  meta: boolean;
  calls: number;
  lastLatencyMs: number;
  totalLatencyMs: number;
}

export interface RelayState {
  segment: string | null;
  models: HudModel[];
}

export const emptyRelay: RelayState = { segment: null, models: [] };

/** Whether a model id belongs to Meta (lights up) or not (greyed out). */
export function isMetaModel(model: string): boolean {
  const id = model.toLowerCase();
  return META_PREFIXES.some((prefix) => id.startsWith(prefix) || id.includes(`/${prefix}`));
}

/** Fold one `relay.model_used` event into the HUD state. */
export function reduceRelay(state: RelayState, event: ModelUsed): RelayState {
  const sameSegment = state.segment === event.segment;
  const models = sameSegment ? state.models : [];
  const existing = models.find((m) => m.model === event.model);
  const updated: HudModel = existing
    ? {
        ...existing,
        calls: existing.calls + 1,
        lastLatencyMs: event.latency_ms,
        totalLatencyMs: existing.totalLatencyMs + event.latency_ms,
      }
    : {
        model: event.model,
        meta: isMetaModel(event.model),
        calls: 1,
        lastLatencyMs: event.latency_ms,
        totalLatencyMs: event.latency_ms,
      };
  return {
    segment: event.segment,
    models: existing ? models.map((m) => (m === existing ? updated : m)) : [...models, updated],
  };
}

/** Number of distinct Meta models that ran in the current segment (the HUD counter). */
export function metaCount(state: RelayState): number {
  return state.models.filter((m) => m.meta).length;
}
