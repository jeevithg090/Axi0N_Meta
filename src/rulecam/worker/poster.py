"""Printable A4 poster with the QR code to the worker rules page (task P2-11).

The same plan, camera, language and base URL always give byte-identical HTML: no timestamps, and
the QR code is the deterministic SVG of `cards.render.qr_svg`. Plan text (site name,
back-translations) is autoescaped; the QR SVG is the only markup inserted.
"""

from __future__ import annotations

from typing import Final

import jinja2

from rulecam.cards.build import worker_url
from rulecam.cards.render import qr_svg
from rulecam.contracts import SitePlan
from rulecam.worker.models import NEVER_LIST
from rulecam.worker.page import camera_rules, page_language, plain_language

_TEXT: Final[dict[str, dict[str, str]]] = {
    "en": {
        "title": "A safety camera works here",
        "watches": "What this camera checks",
        "never": "What it never does",
        "retention": "Events are kept for {days} days, then deleted.",
        "anonymous": "Alerts are anonymous. Scan to read the rules and dispute an alert.",
        "untranslated": "(not yet described in this language)",
        "faces": "No face recognition",
        "names": "No names or identities",
        "speed_scores": "No speed or productivity scores",
        "emotion": "No emotion or health guesses",
        "break_rooms": "No cameras in break rooms or restrooms",
    },
    "es": {
        "title": "Aquí funciona una cámara de seguridad",
        "watches": "Qué revisa esta cámara",
        "never": "Lo que nunca hace",
        "retention": "Los eventos se guardan {days} días y luego se borran.",
        "anonymous": "Las alertas son anónimas. Escanee para ver las reglas y disputar una alerta.",
        "untranslated": "(aún no descrita en este idioma)",
        "faces": "Sin reconocimiento facial",
        "names": "Sin nombres ni identidades",
        "speed_scores": "Sin puntuación de velocidad o productividad",
        "emotion": "Sin suposiciones sobre emociones o salud",
        "break_rooms": "Sin cámaras en salas de descanso o baños",
    },
}

POSTER_TEMPLATE: Final = """<!DOCTYPE html>
<html lang="{{ lang }}">
<head>
  <meta charset="UTF-8">
  <title>{{ t.title }}: {{ site_name }}</title>
  <style>
    @page { size: A4 portrait; margin: 16mm; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, "Segoe UI", Roboto, sans-serif; color: #0f172a; }
    h1 { font-size: 30pt; margin: 0 0 4pt; }
    h2 { font-size: 16pt; margin: 18pt 0 6pt; }
    .where { font-size: 13pt; color: #334155; }
    li { font-size: 13pt; margin: 4pt 0; }
    .never { border-left: 5pt solid #b91c1c; padding: 2pt 12pt; }
    .qr { margin-top: 20pt; text-align: center; }
    .qr svg { width: 70mm; height: 70mm; }
    .url { font-family: monospace; font-size: 10pt; word-break: break-all; }
  </style>
</head>
<body>
  <h1>{{ t.title }}</h1>
  <p class="where">{{ site_name }} · {{ camera_id }}</p>
  <h2>{{ t.watches }}</h2>
  <ul>
{% for rule in rules %}
    <li>{{ rule if rule is not none else t.untranslated }}</li>
{% endfor %}
  </ul>
  <h2>{{ t.never }}</h2>
  <ul class="never">
{% for item in never %}
    <li>{{ t[item] }}</li>
{% endfor %}
  </ul>
  <p>{{ t.retention.format(days=retention_days) }}</p>
  <div class="qr">
    {{ qr }}
    <p>{{ t.anonymous }}</p>
    <p class="url">{{ url }}</p>
  </div>
</body>
</html>
"""

_env = jinja2.Environment(
    autoescape=True,
    undefined=jinja2.StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_template = _env.from_string(POSTER_TEMPLATE)


def render_poster(plan: SitePlan, camera_id: str, lang: str | None, base_url: str) -> str:
    """Render the A4 poster for one camera.

    Args:
        plan: The site plan.
        camera_id: A camera the plan watches.
        lang: The poster's language (the plan's first language when absent or unknown).
        base_url: Public base URL of the worker page (the UI's origin).

    Returns:
        The HTML document.

    Raises:
        CardError: If `base_url` is not an absolute http(s) URL.
    """
    lang = page_language(plan, lang)
    url = worker_url(base_url, plan.plan_id, camera_id, lang)
    rules = [plain_language(plan, rule.id, lang) for rule in camera_rules(plan, camera_id)]
    return _template.render(
        lang=lang,
        t=_TEXT.get(lang, _TEXT["en"]),
        site_name=plan.site_name,
        camera_id=camera_id,
        rules=rules,
        never=NEVER_LIST,
        retention_days=plan.retention_days,
        qr=qr_svg(url),
        url=url,
    )
