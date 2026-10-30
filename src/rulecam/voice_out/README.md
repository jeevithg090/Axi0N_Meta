# `rulecam.voice_out`: Meta Voice Out

Every spoken warning is rendered once, when a plan is approved, and checked by transcribing it back
before any loudspeaker plays it (task P1-11, master plan §3.4).

```
phrase (en) ── StructuredLLM (Glimmer) ──▶ translation ── TTS (MMS-TTS) ──▶ data/tts/<sha256>.wav
                                                                              │
                         RoundTripResult ◀── WER ≤ 0.25? ◀── Transcriber (Voice Transcribe)
```

## Public API

| Name | What it does |
|---|---|
| `MmsTts(bus)` | `TTS` using Meta MMS-TTS (`facebook/mms-tts-<iso3>`) through `transformers.VitsModel`, on CPU. Each language's model loads on first use and stays cached. Output is a 16 kHz mono 16-bit WAV. Publishes `relay.model_used` with `meta=True`. |
| `SystemTts(bus)` | Fallback `TTS` through macOS `say` or `espeak-ng`, run as an argument list with the text read from a temporary file. Publishes `relay.model_used` with `meta=False` so the HUD greys it out. |
| `FallbackTts(primary, fallback)` | Uses `fallback` whenever `primary` raises a `VoiceOutError`, for example for a language MMS lacks. |
| `mms_code(lang)`, `MMS_CODES` | ISO-639-1 → MMS (ISO-639-3) table; region suffixes are ignored (`es-MX` → `spa`). |
| `Prerenderer(tts, translator)` | For each (phrase, crew language) pair: translate with one fixed template (the phrase is passed as data), synthesise, and cache to `<RULECAM_DATA_DIR>/tts/<cache_key>.wav`. Cached phrases are never synthesised again. |
| `cache_key(text, lang, voice=)` | sha256 over canonical JSON of the NFC text, the primary language subtag, the voice and a format version. |
| `RoundTripChecker(transcriber)` | Transcribes a phrase back and returns a `RoundTripResult`. Failures carry `reason="voice_roundtrip_failed"` for Rule CI (P2-08). |
| `word_error_rate(ref, hyp)` | WER after lower-casing and stripping punctuation and accents. |

Typical wiring:

```python
bus = Bus()
tts = FallbackTts(MmsTts(bus), SystemTts(bus))
phrases = Prerenderer(tts, glimmer).prerender(rule_phrases, plan.languages)
results = [RoundTripChecker(voice_transcribe).check_phrase(p) for p in phrases]
failed = [r for r in results if not r.passed]
```

`torch` and `transformers` come from the `ml` extra (`uv sync --extra ml`) and are imported only
when an MMS model is first loaded, so this package imports without them. MMS checkpoints are
public; no token is needed. Korean and Amharic MMS voices need the optional `uroman` romaniser,
which is not installed, so `MmsTts` raises `UnsupportedLanguageError` for them and `FallbackTts`
switches engines.

The round trip only ever transcribes audio this package synthesised, never microphone or camera
audio (guardrail R13).

## Licence

MMS-TTS weights are **CC-BY-NC 4.0** (non-commercial). They are fine for the hackathon and listed
in `WHAT-WE-BUILT.md`; a commercial release needs another engine.

## Tests

```bash
uv run pytest tests/voice_out                 # fakes only; no network, no model download
uv sync --extra ml && uv run pytest tests/voice_out   # also runs a tiny offline VITS model
uv run pytest -m live tests/voice_out         # real facebook/mms-tts-spa (downloads ~145 MB)
```
