// The worker rules page (P2-11): payload types from `rulecam.worker.models`, the page's data
// mapping and its English/Spanish strings. Rule text is model output: React escapes it.

export type DisputeOutcome = "pending" | "upheld" | "rejected";
export type NeverItem = "faces" | "names" | "speed_scores" | "emotion" | "break_rooms";

export const DISPUTE_REASONS = [
  "lookalike_object",
  "glare_or_shadow",
  "wrong_place_or_distance",
  "rule_was_followed",
  "other",
] as const;
export type DisputeReason = (typeof DISPUTE_REASONS)[number];
export const MAX_REASON_TEXT = 500;

export interface RuleCounts {
  alerts: number;
  disputes: number;
  upheld: number;
}

export interface WorkerRule {
  rule_id: string;
  plain_language: string | null;
}

export interface EventCard {
  code: string;
  rule_id: string;
  bucket_start: string;
  dispute: DisputeOutcome | null;
}

export interface WorkerPage {
  plan_id: string;
  site_name: string;
  camera_id: string;
  lang: string;
  languages: string[];
  privacy_mode: "standard" | "anonymous" | "strict";
  retention_days: number;
  never_list: NeverItem[];
  rules: WorkerRule[];
  events: EventCard[];
  counts: Record<string, RuleCounts>;
}

export interface DisputeRequest {
  event_code: string;
  reason_choice: DisputeReason;
  reason_text: string;
}

export interface DisputeResult {
  code: string;
  rule_id: string;
  outcome: DisputeOutcome;
  counts: RuleCounts;
}

export interface RuleRow {
  ruleId: string;
  text: string | null;
  counts: RuleCounts;
}

export interface EventRow {
  code: string;
  ruleId: string;
  ruleText: string | null;
  bucketStart: Date;
  dispute: DisputeOutcome | null;
}

const NO_COUNTS: RuleCounts = { alerts: 0, disputes: 0, upheld: 0 };
const BUCKET_MS = 15 * 60 * 1000;

/** Rules with their counts, and events with their rule's text, in the API's order. */
export function workerView(page: WorkerPage): { rules: RuleRow[]; events: EventRow[] } {
  const text = new Map(page.rules.map((r) => [r.rule_id, r.plain_language]));
  return {
    rules: page.rules.map((r) => ({
      ruleId: r.rule_id,
      text: r.plain_language,
      counts: page.counts[r.rule_id] ?? NO_COUNTS,
    })),
    events: page.events.map((e) => ({
      code: e.code,
      ruleId: e.rule_id,
      ruleText: text.get(e.rule_id) ?? null,
      bucketStart: new Date(e.bucket_start),
      dispute: e.dispute,
    })),
  };
}

/** "Fri 10:15–10:30" in the viewer's time zone. */
export function bucketLabel(start: Date, lang: string, timeZone?: string): string {
  const day = new Intl.DateTimeFormat(lang, { weekday: "short", timeZone }).format(start);
  const time = new Intl.DateTimeFormat(lang, {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  });
  const end = new Date(start.getTime() + BUCKET_MS);
  return `${day} ${time.format(start)}–${time.format(end)}`;
}

export type UiLang = "en" | "es";

/** The UI language for a plan language (Spanish for any `es*`, else English). */
export function uiLang(lang: string): UiLang {
  return lang.toLowerCase().startsWith("es") ? "es" : "en";
}

export interface WorkerStrings {
  heading: string;
  language: string;
  loading: string;
  loadFailed: string;
  watches: string;
  notTranslated: string;
  never: string;
  neverItems: Record<NeverItem, string>;
  retention: (days: number) => string;
  privacy: Record<WorkerPage["privacy_mode"], string>;
  counts: { alerts: string; disputes: string; upheld: string };
  events: string;
  noEvents: string;
  dispute: string;
  disputed: Record<DisputeOutcome, string>;
  sheetTitle: (code: string) => string;
  reason: string;
  reasons: Record<DisputeReason, string>;
  details: string;
  detailsHint: string;
  anonymous: string;
  submit: string;
  sending: string;
  close: string;
  outcomes: Record<DisputeOutcome, string>;
  errors: { rateLimited: string; notFound: string; failed: string };
}

export const STRINGS: Record<UiLang, WorkerStrings> = {
  en: {
    heading: "Safety camera rules",
    language: "Language",
    loading: "Loading…",
    loadFailed: "This page could not be loaded. Try again later.",
    watches: "What this camera checks",
    notTranslated: "Not yet described in this language.",
    never: "What it never does",
    neverItems: {
      faces: "No face recognition",
      names: "No names or identities",
      speed_scores: "No speed or productivity scores",
      emotion: "No emotion or health guesses",
      break_rooms: "No cameras in break rooms or restrooms",
    },
    retention: (days) => `Events are kept for ${days} days, then deleted.`,
    privacy: {
      standard: "Clips may show the scene as filmed.",
      anonymous: "Clips show stick figures, not people.",
      strict: "Clips show stick figures only, and are never sent off site.",
    },
    counts: { alerts: "Alerts", disputes: "Disputes", upheld: "Upheld" },
    events: "Recent alerts (anonymous)",
    noEvents: "No alerts in this period.",
    dispute: "Dispute",
    disputed: { pending: "Dispute pending", upheld: "Dispute upheld", rejected: "Not upheld" },
    sheetTitle: (code) => `Dispute alert ${code}`,
    reason: "Why was this alert wrong?",
    reasons: {
      lookalike_object: "Something looked like it (e.g. a backpack strap)",
      glare_or_shadow: "Glare or shadow",
      wrong_place_or_distance: "Wrong place or distance",
      rule_was_followed: "The rule was followed",
      other: "Other",
    },
    details: "Details (optional)",
    detailsHint: "Do not write names.",
    anonymous: "Anonymous: no name, address or device is stored.",
    submit: "Send dispute",
    sending: "Sending…",
    close: "Close",
    outcomes: {
      upheld: "Upheld: the alert was a false alarm. The counts are updated.",
      rejected: "Not upheld: the reviewer found the alert correct.",
      pending: "Received: a person will review it.",
    },
    errors: {
      rateLimited: "Too many disputes today. Try again tomorrow.",
      notFound: "This alert is no longer available.",
      failed: "The dispute could not be sent. Try again.",
    },
  },
  es: {
    heading: "Reglas de la cámara de seguridad",
    language: "Idioma",
    loading: "Cargando…",
    loadFailed: "No se pudo cargar esta página. Inténtelo más tarde.",
    watches: "Qué revisa esta cámara",
    notTranslated: "Aún no descrita en este idioma.",
    never: "Lo que nunca hace",
    neverItems: {
      faces: "Sin reconocimiento facial",
      names: "Sin nombres ni identidades",
      speed_scores: "Sin puntuación de velocidad o productividad",
      emotion: "Sin suposiciones sobre emociones o salud",
      break_rooms: "Sin cámaras en salas de descanso o baños",
    },
    retention: (days) => `Los eventos se guardan ${days} días y luego se borran.`,
    privacy: {
      standard: "Los clips pueden mostrar la escena tal como se grabó.",
      anonymous: "Los clips muestran figuras de palitos, no personas.",
      strict: "Los clips solo muestran figuras de palitos y nunca salen del sitio.",
    },
    counts: { alerts: "Alertas", disputes: "Disputas", upheld: "Aceptadas" },
    events: "Alertas recientes (anónimas)",
    noEvents: "No hay alertas en este periodo.",
    dispute: "Disputar",
    disputed: {
      pending: "Disputa pendiente",
      upheld: "Disputa aceptada",
      rejected: "No aceptada",
    },
    sheetTitle: (code) => `Disputar alerta ${code}`,
    reason: "¿Por qué la alerta fue un error?",
    reasons: {
      lookalike_object: "Algo se parecía (p. ej. la correa de una mochila)",
      glare_or_shadow: "Reflejo o sombra",
      wrong_place_or_distance: "Lugar o distancia equivocados",
      rule_was_followed: "Se cumplió la regla",
      other: "Otro",
    },
    details: "Detalles (opcional)",
    detailsHint: "No escriba nombres.",
    anonymous: "Anónimo: no se guarda nombre, dirección ni dispositivo.",
    submit: "Enviar disputa",
    sending: "Enviando…",
    close: "Cerrar",
    outcomes: {
      upheld: "Aceptada: la alerta fue una falsa alarma. Los recuentos se actualizaron.",
      rejected: "No aceptada: el revisor confirmó la alerta.",
      pending: "Recibida: una persona la revisará.",
    },
    errors: {
      rateLimited: "Demasiadas disputas hoy. Inténtelo mañana.",
      notFound: "Esta alerta ya no está disponible.",
      failed: "No se pudo enviar la disputa. Inténtelo de nuevo.",
    },
  },
};

/** The message for a failed dispute, by HTTP status. */
export function disputeError(status: number, strings: WorkerStrings): string {
  if (status === 429) return strings.errors.rateLimited;
  if (status === 404) return strings.errors.notFound;
  return strings.errors.failed;
}
