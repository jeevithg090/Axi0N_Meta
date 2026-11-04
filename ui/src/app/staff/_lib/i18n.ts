// The staff app's own words in English, Spanish and Hindi. Alert content comes from the rules
// and the models; Muse Spark writes its spoken line in the chosen language on the server.

import type { ItemStatus, Lang, Source, Stage, StaffAction } from "./types";

export const LANG_NAMES: Record<Lang, string> = {
  en: "English",
  es: "Español",
  hi: "हिन्दी",
};

/** BCP-47 tags for the phone's own speech synthesis (the fallback voice). */
export const SPEECH_TAGS: Record<Lang, string> = { en: "en-US", es: "es-ES", hi: "hi-IN" };

export interface Words {
  appName: string;
  chooseLanguage: string;
  chooseRole: string;
  roleHint: string;
  otherRole: string;
  start: string;
  allowAlerts: string;
  feed: string;
  stats: string;
  settings: string;
  everyone: string;
  nothingOpen: string;
  nothingOpenHint: string;
  live: string;
  offline: string;
  reconnecting: string;
  dryRun: string;
  sample: string;
  actions: Record<StaffAction, string>;
  status: Record<ItemStatus, string>;
  source: Record<Source, string>;
  stage: Record<Stage, string>;
  escalatesIn: string;
  escalatesTo: string;
  verifiedBy: string;
  readAloud: string;
  holdToAsk: string;
  listening: string;
  thinking: string;
  typeQuestion: string;
  send: string;
  scanShelf: string;
  scanning: string;
  addPhoto: string;
  skipPhoto: string;
  doneTitle: string;
  escalateTo: string;
  cancel: string;
  timeToAck: string;
  timeToFix: string;
  loop: string;
  byRole: string;
  byShift: string;
  items: string;
  acked: string;
  median: string;
  p90: string;
  phone: string;
  autoRead: string;
  changeRole: string;
  open: string;
  closed: string;
  doNow: string;
  announced: string;
  heard: string;
  noRole: string;
  reportNearMiss: string;
  needsPerson: string;
  notChecked: string;
  measured: string;
  sentTo: string;
  pushOn: string;
  proofHint: string;
}

const en: Words = {
  appName: "Rulecam Staff",
  chooseLanguage: "Language",
  chooseRole: "Your role on this shift",
  roleHint: "Alerts go to roles, never to names.",
  otherRole: "Other role",
  start: "Start",
  allowAlerts: "Allow alerts on this phone",
  feed: "Alerts",
  stats: "Response",
  settings: "Settings",
  everyone: "All roles",
  nothingOpen: "Nothing needs you right now",
  nothingOpenHint: "New alerts appear here, buzz and are read aloud.",
  live: "Live",
  offline: "Offline: last known alerts",
  reconnecting: "Reconnecting",
  dryRun: "Dry run: rehearsal, nothing was switched",
  sample: "SAMPLE",
  actions: {
    acknowledge: "Acknowledge",
    on_my_way: "On my way",
    done: "Done",
    false_alarm: "False alarm",
    escalate: "Escalate",
  },
  status: {
    open: "Open",
    acknowledged: "Acknowledged",
    on_my_way: "On the way",
    done: "Done",
    false_alarm: "False alarm",
  },
  source: {
    rule: "Rule",
    crowd: "Crowd",
    shelf: "Shelf",
    review: "Needs review",
    drill: "Drill",
  },
  stage: {
    raised: "Raised",
    verified: "Verified",
    assigned: "Sent",
    acknowledged: "Acknowledged",
    on_my_way: "On the way",
    escalated: "Escalated",
    record: "Recorded",
    done: "Done",
    false_alarm: "False alarm",
  },
  escalatesIn: "Escalates in",
  escalatesTo: "to",
  verifiedBy: "Checked by",
  readAloud: "Read aloud",
  holdToAsk: "Hold to ask",
  listening: "Listening… release to send",
  thinking: "Finding the answer…",
  typeQuestion: "Or type a question",
  send: "Send",
  scanShelf: "Scan a shelf",
  scanning: "Checking the shelf…",
  addPhoto: "Add a photo of the fix",
  skipPhoto: "Done without photo",
  doneTitle: "Mark done",
  escalateTo: "Send to which role?",
  cancel: "Cancel",
  timeToAck: "to acknowledge",
  timeToFix: "to fix",
  loop: "From camera to action",
  byRole: "By role",
  byShift: "By shift",
  items: "alerts",
  acked: "answered",
  median: "median",
  p90: "slowest 10%",
  phone: "alert to phone",
  autoRead: "Read new alerts aloud",
  changeRole: "Change role",
  open: "Open",
  closed: "Closed",
  doNow: "Do now",
  announced: "Announced",
  heard: "You asked",
  noRole: "Pick a role first",
  reportNearMiss: "Report a near miss",
  needsPerson: "A person must check",
  notChecked: "not checked (verifier down)",
  measured: "Measured",
  sentTo: "Sent to",
  pushOn: "Alerts reach this phone even when the app is closed",
  proofHint: "Muse Spark and Glimmer check the photo before the task closes.",
};

const es: Words = {
  appName: "Rulecam Personal",
  chooseLanguage: "Idioma",
  chooseRole: "Tu puesto en este turno",
  roleHint: "Las alertas van a puestos, nunca a nombres.",
  otherRole: "Otro puesto",
  start: "Empezar",
  allowAlerts: "Permitir alertas en este teléfono",
  feed: "Alertas",
  stats: "Respuesta",
  settings: "Ajustes",
  everyone: "Todos los puestos",
  nothingOpen: "Nada te necesita ahora",
  nothingOpenHint: "Las alertas nuevas aparecen aquí, vibran y se leen en voz alta.",
  live: "En vivo",
  offline: "Sin conexión: últimas alertas",
  reconnecting: "Reconectando",
  dryRun: "Simulacro: no se activó nada",
  sample: "MUESTRA",
  actions: {
    acknowledge: "Recibido",
    on_my_way: "Voy en camino",
    done: "Hecho",
    false_alarm: "Falsa alarma",
    escalate: "Escalar",
  },
  status: {
    open: "Abierta",
    acknowledged: "Recibida",
    on_my_way: "En camino",
    done: "Hecha",
    false_alarm: "Falsa alarma",
  },
  source: {
    rule: "Regla",
    crowd: "Multitud",
    shelf: "Estante",
    review: "Revisar",
    drill: "Simulacro",
  },
  stage: {
    raised: "Detectada",
    verified: "Verificada",
    assigned: "Enviada",
    acknowledged: "Recibida",
    on_my_way: "En camino",
    escalated: "Escalada",
    record: "Registrada",
    done: "Hecha",
    false_alarm: "Falsa alarma",
  },
  escalatesIn: "Se escala en",
  escalatesTo: "a",
  verifiedBy: "Verificado por",
  readAloud: "Leer en voz alta",
  holdToAsk: "Mantén para preguntar",
  listening: "Escuchando… suelta para enviar",
  thinking: "Buscando la respuesta…",
  typeQuestion: "O escribe una pregunta",
  send: "Enviar",
  scanShelf: "Revisar un estante",
  scanning: "Revisando el estante…",
  addPhoto: "Añadir foto del arreglo",
  skipPhoto: "Hecho sin foto",
  doneTitle: "Marcar hecho",
  escalateTo: "¿A qué puesto enviarlo?",
  cancel: "Cancelar",
  timeToAck: "para recibir",
  timeToFix: "para resolver",
  loop: "De la cámara a la acción",
  byRole: "Por puesto",
  byShift: "Por turno",
  items: "alertas",
  acked: "respondidas",
  median: "mediana",
  p90: "10% más lento",
  phone: "alerta al teléfono",
  autoRead: "Leer alertas nuevas en voz alta",
  changeRole: "Cambiar puesto",
  open: "Abiertas",
  closed: "Cerradas",
  doNow: "Haz ahora",
  announced: "Anunciado",
  heard: "Preguntaste",
  noRole: "Elige un puesto primero",
  reportNearMiss: "Reportar un casi accidente",
  needsPerson: "Una persona debe revisarlo",
  notChecked: "sin verificar (verificador caído)",
  measured: "Medido",
  sentTo: "Enviado a",
  pushOn: "Las alertas llegan aunque la app esté cerrada",
  proofHint: "Muse Spark y Glimmer revisan la foto antes de cerrar la tarea.",
};

const hi: Words = {
  appName: "Rulecam स्टाफ़",
  chooseLanguage: "भाषा",
  chooseRole: "इस शिफ़्ट में आपकी भूमिका",
  roleHint: "अलर्ट भूमिकाओं को जाते हैं, नामों को कभी नहीं।",
  otherRole: "दूसरी भूमिका",
  start: "शुरू करें",
  allowAlerts: "इस फ़ोन पर अलर्ट की अनुमति दें",
  feed: "अलर्ट",
  stats: "प्रतिक्रिया",
  settings: "सेटिंग्स",
  everyone: "सभी भूमिकाएँ",
  nothingOpen: "अभी आपके लिए कुछ नहीं है",
  nothingOpenHint: "नए अलर्ट यहाँ दिखेंगे, फ़ोन काँपेगा और पढ़कर सुनाए जाएँगे।",
  live: "लाइव",
  offline: "ऑफ़लाइन: पिछले अलर्ट",
  reconnecting: "फिर से जुड़ रहा है",
  dryRun: "अभ्यास: कुछ चालू नहीं किया गया",
  sample: "नमूना",
  actions: {
    acknowledge: "देख लिया",
    on_my_way: "आ रहा हूँ",
    done: "हो गया",
    false_alarm: "गलत अलर्ट",
    escalate: "आगे भेजें",
  },
  status: {
    open: "खुला",
    acknowledged: "देखा गया",
    on_my_way: "रास्ते में",
    done: "हो गया",
    false_alarm: "गलत अलर्ट",
  },
  source: {
    rule: "नियम",
    crowd: "भीड़",
    shelf: "शेल्फ़",
    review: "जाँच ज़रूरी",
    drill: "अभ्यास",
  },
  stage: {
    raised: "पकड़ा गया",
    verified: "जाँचा गया",
    assigned: "भेजा गया",
    acknowledged: "देखा गया",
    on_my_way: "रास्ते में",
    escalated: "आगे भेजा",
    record: "दर्ज",
    done: "हो गया",
    false_alarm: "गलत अलर्ट",
  },
  escalatesIn: "आगे जाएगा",
  escalatesTo: "को",
  verifiedBy: "जाँच",
  readAloud: "पढ़कर सुनाएँ",
  holdToAsk: "पूछने के लिए दबाकर रखें",
  listening: "सुन रहा है… छोड़ें तो भेजें",
  thinking: "जवाब ढूँढ रहा है…",
  typeQuestion: "या सवाल लिखें",
  send: "भेजें",
  scanShelf: "शेल्फ़ जाँचें",
  scanning: "शेल्फ़ जाँच रहा है…",
  addPhoto: "काम की फ़ोटो जोड़ें",
  skipPhoto: "बिना फ़ोटो के पूरा",
  doneTitle: "पूरा करें",
  escalateTo: "किस भूमिका को भेजें?",
  cancel: "रद्द करें",
  timeToAck: "देखने में",
  timeToFix: "ठीक करने में",
  loop: "कैमरे से कार्रवाई तक",
  byRole: "भूमिका के अनुसार",
  byShift: "शिफ़्ट के अनुसार",
  items: "अलर्ट",
  acked: "जवाब दिए",
  median: "औसत",
  p90: "सबसे धीमे 10%",
  phone: "अलर्ट से फ़ोन तक",
  autoRead: "नए अलर्ट पढ़कर सुनाएँ",
  changeRole: "भूमिका बदलें",
  open: "खुले",
  closed: "बंद",
  doNow: "अभी करें",
  announced: "घोषणा",
  heard: "आपने पूछा",
  noRole: "पहले भूमिका चुनें",
  reportNearMiss: "बाल-बाल बचे हादसे की रिपोर्ट",
  needsPerson: "किसी व्यक्ति को जाँचना होगा",
  notChecked: "जाँच नहीं हुई (वेरिफ़ायर बंद)",
  measured: "माप",
  sentTo: "भेजा गया",
  pushOn: "ऐप बंद होने पर भी अलर्ट इस फ़ोन पर आएँगे",
  proofHint: "काम बंद होने से पहले Muse Spark और Glimmer फ़ोटो जाँचते हैं।",
};

export const WORDS: Record<Lang, Words> = { en, es, hi };

/** "1 m 05 s", "42 s", "1 h 3 m": short durations staff read at a glance. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "–";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} m ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(m / 60)} h ${m % 60} m`;
}

/** A countdown as m:ss (never negative). */
export function countdown(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** A role identifier as words (`store_manager` → `store manager`). */
export function roleWords(role: string): string {
  return role.replace(/[_-]+/g, " ");
}

/** A role typed by a person as the identifier the API accepts, or null. */
export function toRoleId(text: string): string | null {
  const id = text
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_")
    .replace(/[^a-z0-9_-]/g, "");
  return /^[a-z][a-z0-9_-]{0,63}$/.test(id) ? id : null;
}
