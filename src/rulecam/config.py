"""Application settings. This is the only module that reads environment variables (§5.3)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration, loaded from the environment and `.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    rulecam_mode: Literal["live", "record", "replay"] = "replay"
    rulecam_data_dir: Path = Path("./data")
    rulecam_replay_dir: Path | None = None
    rulecam_site_config: Path | None = None  # owner's site YAML (docs/site.example.yaml)
    rulecam_runtime: bool = False  # start the runtime loop (SAM, tracker, Glimmer) with the API
    # The git SHA served in GET /health; unset, the REVISION file `make ship-api` writes is read.
    rulecam_revision: str | None = None
    # Runtime SAM cost control (docs/deploy.md "Runtime SAM cost"): while nothing is happening on a
    # camera, segment one chunk in N, per the strictest severity of the rules on that camera.
    rulecam_idle_stride_low: int = Field(default=3, ge=1)
    rulecam_idle_stride_medium: int = Field(default=3, ge=1)
    rulecam_idle_stride_high: int = Field(default=2, ge=1, le=2)  # HIGH: every 2nd chunk at most
    rulecam_idle_holdoff_s: float = Field(default=30.0, ge=0)  # every chunk for 30 s after activity
    # Per-camera dollar budget (runtime.cost): hosted SAM spend over the last minute as $/h, plus
    # the camera's share of the GPU pods. At or above it: alarm, and with hosted spend, degrade
    # to one chunk in RULECAM_SAM_BUDGET_STRIDE. 12 $/h = the former 1,000 hosted frames/min.
    rulecam_budget_usd_per_camera_hour: float = Field(default=12.0, ge=0)  # 0 = no budget
    rulecam_gpu_usd_per_hour: float = Field(default=0.0, ge=0)  # GPU pods' combined $/h
    # One running RunPod serverless worker's $/h (RTX 4090 pool); with serverless GPUs whose
    # worker state is known, only running workers are charged (runtime.serverless).
    rulecam_gpu_worker_usd_per_hour: float = Field(default=1.34, ge=0)
    rulecam_sam_budget_frames_per_min: int = Field(default=0, ge=0)  # optional frame cap; 0 = off
    rulecam_sam_budget_stride: int = Field(default=3, ge=1)  # over budget: one chunk in N
    # Stop the GPU pods after this many seconds with no camera on an active plan
    # (runtime.idle_stop); 0 = never. Needs the RunPod key in RULECAM_RUNPOD_API_KEY_FILE.
    rulecam_idle_stop_gpu_after_s: float = Field(default=0.0, ge=0)
    rulecam_idle_stop_gpu_pods: str = "rulecam-glimmer,rulecam-sam"  # pod names or ids
    rulecam_runpod_api_key_file: Path | None = None
    rulecam_runtime_concept_workers: int = Field(default=4, ge=1)  # concepts segmented at once
    rulecam_runtime_pipeline_depth: int = Field(default=4, ge=1)  # chunks per camera in flight
    # Runtime SAM calls open at once, across cameras (docs/deploy.md "Stage timings").
    rulecam_sam_max_concurrency: int = Field(default=8, ge=1)
    rulecam_runtime_verify_workers: int = Field(default=4, ge=1)  # Tier 1 verifications at once
    # Tier 1 retry and circuit breaker (verify.guard): retries after a transient failure, the
    # failures in a row that open the circuit, and how long it stays open before a trial.
    rulecam_verify_retries: int = Field(default=1, ge=0)
    rulecam_verify_breaker_failures: int = Field(default=3, ge=1)
    rulecam_verify_breaker_cooldown_s: float = Field(default=30.0, gt=0)
    # Decode hosted SAM masks on the runtime path (pure-Python, GIL-bound; boxes suffice for feet).
    rulecam_runtime_sam_masks: bool = False
    # Runtime perception (docs/deploy.md "Runtime perception modes"): `image` runs SAM 3.1 image
    # mode on keyframes and joins them with ByteTrack; `video` sends whole chunks to SAM 3.1 video.
    rulecam_runtime_sam_mode: Literal["image", "video"] = "image"
    rulecam_keyframe_fps: float = Field(default=2.0, ge=2.0, le=10.0)  # keyframes per second
    rulecam_keyframe_fps_cameras: str = ""  # per-camera overrides, e.g. "dock=4,kitchen=3"
    # Image mode: the lowest SAM score the keyframe tracker gets (ByteTrack's second pass, which
    # only extends existing tracks); everything else keeps SAM's 0.5 cut.
    rulecam_tracker_low_confidence: float = Field(default=0.1, ge=0.0, le=0.5)
    # Image mode: the tracker for people. `botsort` (BoT-SORT with OSNet-AIN person re-ID vectors
    # from the SAM pod, `perception.botsort`) runs only in prototype mode with the `reid` extra
    # installed; otherwise, and for every other concept, ByteTrack.
    rulecam_person_tracker: Literal["botsort", "bytetrack"] = "botsort"
    # Rule ids whose owner turned operator exclusion off (comma-separated; rules/operators.py):
    # for these, a person riding a forklift counts as a pedestrian next to it.
    rulecam_operator_exclusion_off: str = ""
    # How long a person first seen on board a vehicle is held as its operator pending before
    # being judged operator or pedestrian (rules/operators.py rule 7); 0 turns the hold off.
    rulecam_operator_warmup_s: float = Field(default=1.5, ge=0.0, le=1.5)
    # YuNet face-detector ONNX (opencv_zoo) for blurring faces in evidence strips; without it
    # only the head regions of tracked people are blurred (runtime.strips).
    rulecam_face_model: Path | None = None
    # Record actions as `dry-run: would …` and execute none of them (no power cut, light,
    # speaker or notification) until the owner turns this off (docs/deploy.md "Actions").
    rulecam_actions_dry_run: bool = True
    # Where confirmed false alarms become permanent Rule CI lookalike regressions (P2-09,
    # `rule_ci.hard_negatives`); default `<data dir>/rule_ci/regressions`.
    rulecam_regressions_dir: Path | None = None
    # Prototype mode (`rulecam.prototype`): the planner declines nothing but rules that single
    # people out by race, ethnicity, religion, nationality or sexual orientation; every compiled
    # rule activates (failed checks are caveats); the verifier's verdict is final.
    rulecam_prototype_mode: bool = False
    # The site's time zone (IANA name), for owner questions like "how many customers at 1 pm".
    rulecam_site_timezone: str = "UTC"
    # The site's approximate location for `/context` (Spark's web search is localised by it).
    rulecam_site_city: str = ""
    rulecam_site_region: str = ""
    rulecam_site_country: str = Field(default="", pattern=r"^([A-Za-z]{2})?$")  # ISO 3166-1
    # Crowd density levels for `/crowd`, persons per m² (rulecam.crowd.density).
    rulecam_crowd_busy_per_m2: float = Field(default=2.0, gt=0)
    rulecam_crowd_crowded_per_m2: float = Field(default=4.0, gt=0)
    rulecam_crowd_critical_per_m2: float = Field(default=5.0, gt=0)
    # Context for `POST /ask` (the owner's words, given to the models as they are): what the site
    # is, and camera id -> name pairs ("phone-62=checkout lanes,phone-61=entrance queue").
    rulecam_site_profile: str = ""
    rulecam_camera_names: str = ""
    # Ask's temporal memory: Glimmer captions each camera every N seconds of video; 0 = off.
    rulecam_ask_caption_every_s: float = Field(default=0.0, ge=0.0)
    # Ask's identity memory: join track fragments by DINOv2 appearance on the GPU pod's /embed.
    rulecam_ask_identities: bool = True
    # ... and people by OSNet-AIN person re-ID vectors (`/embed` model "person", add-on 3.2) instead
    # of DINOv2; falls back to DINOv2 when the GPU pod has no re-ID model. Prototype mode only.
    rulecam_ask_person_reid: bool = True
    # How much tool output reaches Spark in Ask (anything cut is reported to it, never silent):
    # rows per SQL query, characters of results per turn, instants per count/look call.
    rulecam_ask_max_rows: int = Field(default=1000, ge=1)
    rulecam_ask_max_result_chars: int = Field(default=100_000, ge=2000)
    rulecam_ask_max_frames: int = Field(default=6, ge=1, le=32)
    # Tool calls one Ask turn runs at the same time (Spark picks how many, up to this).
    rulecam_ask_max_actions: int = Field(default=6, ge=1, le=16)
    # Ask's `watch` tool: the longest window joined into one clip for Spark, and the Spark model
    # that takes video (Spark's video limits are not published; the clip is capped to this).
    rulecam_ask_max_watch_s: float = Field(default=180.0, ge=3.0, le=1800.0)
    rulecam_ask_watch_model: str = "muse-spark-1.2"
    # Muse Spark Open (Baseten) checks each Ask answer against its trace and can send it back
    # once (needs BASETEN_API_KEY; off without it).
    rulecam_ask_review: bool = True
    # Rule extensions (`rulecam.extensions`): Muse Code writes a new check type headless
    # (`muse exec --json`), authenticated once with `muse auth set --api-key-stdin`. Extra
    # arguments go after `exec --json` (shell-split), e.g. an approval mode for file edits.
    rulecam_muse_code_bin: str = "muse"
    rulecam_muse_code_args: str = ""
    rulecam_muse_code_timeout_s: float = Field(default=300.0, gt=0.0, le=1800.0)
    rulecam_muse_code_model: str = "muse-spark-1.2"
    # Chain Mode (`rulecam.chain`): one folder per rule pack (pack.yaml, stores/, simulation.yaml)
    # served by the owner-only `/chain` routes.
    rulecam_chains_dir: Path = Path("chains")
    # The deployed commit, for Ask's run log (empty: `git rev-parse HEAD` when run from a clone).
    rulecam_git_sha: str = ""
    # Crowd early warning (`rulecam.crowd`, prototype mode): measure each camera's crowd every N
    # seconds of video (SAM 3.1 on grid x grid tiles counting the concept); 0 = off.
    rulecam_crowd_every_s: float = Field(default=0.0, ge=0.0)
    rulecam_crowd_concept: str = "person"
    rulecam_crowd_grid: int = Field(default=2, ge=1, le=4)
    # Seconds between the warden's assessments (Muse Spark) of a watched camera.
    rulecam_crowd_assess_every_s: float = Field(default=10.0, gt=0.0)
    # Seconds Muse Glimmer gets to check an alert's frames; past it the alert goes unchecked.
    rulecam_crowd_check_timeout_s: float = Field(default=20.0, gt=0.0)
    # Cameras to measure (comma-separated ids; empty: every camera with video). With an
    # instruction, they are watched from start-up in these announcement languages ("hi,en").
    rulecam_crowd_cameras: str = ""
    rulecam_crowd_instruction: str = ""
    rulecam_crowd_languages: str = ""
    # Only for command-line clients that log in to a remote API as the owner (`rulecam eval
    # ask`); the API itself never reads it (it checks OWNER_PASSPHRASE_HASH).
    rulecam_owner_passphrase: SecretStr | None = None

    # Evidence strips sent to Glimmer: frames per strip and their width in pixels.
    rulecam_strip_frames: int = Field(default=6, ge=1, le=12)
    rulecam_strip_max_width: int = Field(default=640, ge=320, le=1920)
    # RTMPose-l body7 ONNX (`scripts/fetch_rtmpose.sh`; runs on the CPU, needs `onnxruntime`):
    # strips then draw each person's real pose instead of a figure fitted to the box. Unset, or
    # a file whose sha256 is not RULECAM_POSE_SHA256 (pose.keypoints.RTMPOSE_BODY7_ONNX_SHA256),
    # means no pose.
    rulecam_pose_onnx: Path | None = None
    rulecam_pose_sha256: str = "cff059fd58a2c0d5fabaddcd66a96abcfb327563bcb0149ea59c9de4a8990fe2"
    # Crop-zoom SAM 3.1 (add-on 3.3): look for small worn items (hats, hair nets, gloves) on
    # enlarged head and hand crops of each person, at probe time and as Tier 0 before Glimmer.
    # Served by the GPU pod's /segment_crops when SAM_BASE_URL is set, else by hosted SAM.
    rulecam_crop_zoom: bool = True
    # Ground calibration from MoGe-2 on the GPU pod (add-on 3.5): `auto` uses it when the pod's
    # /health says it is installed, `moge` always tries it, `off` keeps only the reference
    # objects and the 1.7 m-person guess. Off in replay mode (its calls were never recorded).
    rulecam_calibration_model: Literal["auto", "moge", "off"] = "auto"

    meta_api_key: SecretStr | None = None
    meta_api_base_url: str | None = None
    baseten_api_key: SecretStr | None = None
    baseten_base_url: str = "https://inference.baseten.co/v1"
    baseten_spark_open_model: str = "meta-models/muse-spark-open-1026"
    spark_open_images_supported: bool = False
    # Muse Spark Open on any OpenAI-compatible server instead of Baseten (wins when set): our own
    # llama-server on a RunPod serverless load balancer (docs/deploy.md "Serverless Muse Spark
    # Open"; the RunPod key in SPARK_OPEN_EDGE_KEY goes in `Authorization`, SPARK_OPEN_API_KEY in
    # `X-Rulecam-Key`) or a hosted provider (no edge key: SPARK_OPEN_API_KEY is the bearer).
    spark_open_base_url: str | None = None
    spark_open_api_key: SecretStr | None = None
    spark_open_edge_key: SecretStr | None = None
    spark_open_model: str = "muse-spark-open"
    spark_open_hardware: str = "runpod-8xH100-llama.cpp-Q4_K_S"
    # State the JSON schema in a system message too: llama-server's Muse Spark template does not
    # enforce `response_format` (checked 4 Nov 2026). Applies to SPARK_OPEN_BASE_URL only.
    spark_open_schema_in_prompt: bool = True
    # Hosted Spark Open used only when the server above gives no usable answer (e.g. Routeway,
    # https://api.routeway.ai/v1, model `muse-spark-open`; its key is the bearer).
    spark_open_fallback_base_url: str | None = None
    spark_open_fallback_api_key: SecretStr | None = None
    spark_open_fallback_model: str = "muse-spark-open"
    # Llama Guard 4 (12B) on an OpenAI-compatible server (`clients.open_weights.llama_guard`):
    # the plan-time screen that refuses rules about who people are. Same key scheme as Spark Open.
    llama_guard_base_url: str | None = None
    llama_guard_api_key: SecretStr | None = None
    llama_guard_edge_key: SecretStr | None = None
    llama_guard_model: str = "llama-guard-4-12b"
    glimmer_base_url: str = "http://127.0.0.1:8080/v1"
    glimmer_api_key: SecretStr | None = None
    # The Glimmer URLs are RunPod serverless load balancers: their edge takes this RunPod API key
    # as `Authorization`, and GLIMMER_API_KEY goes in `X-Rulecam-Key` (`llama_edge` checks it).
    glimmer_edge_key: SecretStr | None = None
    glimmer_model: str = "muse-glimmer-30b"
    # More llama-server URLs serving the same model, comma-separated: Tier 1 calls are spread
    # over all of them, least busy first, with failover (`clients.transport.PooledTransport`).
    glimmer_extra_base_urls: str = ""
    glimmer_quant: str = ""
    glimmer_hardware: str = ""
    # Hosted OpenAI-compatible Glimmer (e.g. Routeway) may honour JSON mode but not a strict
    # schema: also state the schema in a system message. Off for llama-server (grammar-enforced).
    glimmer_schema_in_prompt: bool = False
    # A standby Glimmer endpoint (same keys and model, any region; docs/deploy.md "Standby
    # Glimmer") tried when GLIMMER_BASE_URL and the extra URLs give no usable answer.
    glimmer_standby_base_url: str | None = None
    # Automatic fallback when our Glimmer GPU gives no usable answer (no HTTP answer, 404, 429,
    # 5xx): the same request, rewritten for a hosted OpenAI-compatible Glimmer (e.g. Routeway,
    # https://api.routeway.ai/v1 with model `muse-glimmer-30b`, or `muse-glimmer-30b:free`).
    glimmer_fallback_base_url: str | None = None
    glimmer_fallback_api_key: SecretStr | None = None
    glimmer_fallback_model: str = "muse-glimmer-30b"
    # Glimmer's output cap per call, reasoning included. A reply cut at it (finish_reason
    # `length`) is retried with the cap doubled, up to the ceiling, then reported.
    glimmer_max_tokens: int = Field(default=1024, ge=64, le=32768)
    glimmer_max_tokens_ceiling: int = Field(default=4096, ge=64, le=32768)
    # llama.cpp's per-request `reasoning_budget_tokens` (thinking tokens before the model must
    # answer). -1: not sent, the server's --reasoning-budget applies (unlimited by default); 0: no
    # thinking. Off by default so recorded Tier 1 replies keep their replay keys; pick a value with
    # scripts/eval/glimmer_budget_sweep.py.
    glimmer_reasoning_budget_tokens: int = Field(default=-1, ge=-1, le=32768)
    evidence_dir: Path = Path("./evidence")
    hf_token: SecretStr | None = None
    modal_token_id: SecretStr | None = None
    modal_token_secret: SecretStr | None = None
    sam_base_url: str | None = None  # our own GPU pod running sam_server; wins over Modal
    sam_api_key: SecretStr | None = None
    # SAM_BASE_URL is a RunPod serverless load balancer: its edge takes this RunPod API key as
    # `Authorization`, and SAM_API_KEY goes in `X-Rulecam-Key` to the worker.
    sam_edge_key: SecretStr | None = None
    # Seconds one self-hosted SAM call may take (a RunPod cold start included) and how many tries
    # it gets before the runtime falls back to hosted SAM for that call. 60 s x 2 waits up to
    # 2 minutes on a throttled endpoint; for live demos 25 s x 1 (docs/demo-day.md).
    sam_http_timeout_s: float = Field(default=60.0, gt=0.0, le=300.0)
    sam_http_attempts: int = Field(default=2, ge=1, le=4)
    kasa_plug_host: str | None = None
    kasa_light_host: str | None = None
    whatsapp_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_app_secret: SecretStr | None = None  # verifies X-Hub-Signature-256 on the webhook
    whatsapp_verify_token: SecretStr | None = None  # the webhook subscription handshake
    # role=E.164 pairs, e.g. "aide=15551234567,charge_nurse=15557654321" (kept only in .env)
    whatsapp_recipients: SecretStr | None = None
    whatsapp_api_base: str = "https://graph.facebook.com/v21.0"
    # Telegram alerts (add-on 5.1): the token from BotFather; links come from `/telegram/link`.
    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str | None = None  # without @; asked from getMe when unset
    telegram_api_base: str = "https://api.telegram.org"
    # Signed outbound webhooks (add-on 5.4): `preset=url` pairs (generic, slack, teams), kept only
    # in `.env` because the URLs are secrets, and the HMAC secret that signs every request.
    rulecam_webhooks: SecretStr | None = None
    rulecam_webhook_secret: SecretStr | None = None
    public_tunnel_url: str | None = None
    public_ui_url: str | None = None  # public origin of the web UI (worker page QR on posters)
    # More web UI hosts owner sessions may act from, comma-separated (e.g. a fallback UI host).
    rulecam_ui_extra_origins: str = ""
    # Owner login (docs/deploy.md "Owner login"): `rulecam owner set-passphrase` prints the hash.
    owner_passphrase_hash: SecretStr | None = None
    session_secret: SecretStr | None = None  # signs owner session cookies; 32+ characters
    session_ttl_s: int = Field(default=12 * 3600, ge=300, le=7 * 24 * 3600)
    # Shared with the UI's middleware, which then forwards the browser's IP (per-client limits).
    ui_proxy_secret: SecretStr | None = None
    # Demo sites only: let non-owners watch the live feed and read plans. Never on a real site,
    # where those reads stay owner-only (W-01).
    public_demo: bool = False
    planner_survey_window_s: float = (
        60.0  # seconds of recent chunks the planner samples frames from
    )
    # Without Tier 2 (Spark Open), Muse Spark 1.3 reviews worker disputes with the evidence
    # frames instead of leaving every one pending for a person (`worker.spark_review`).
    rulecam_dispute_spark_fallback: bool = True
    # Chain a `coverage` record when a camera goes BLIND or recovers (`runtime.coverage_log`).
    # Off by default: moving-camera stock clips flap `moved`; turn it on for fixed site cameras.
    rulecam_coverage_log: bool = False
    # Reuse a camera's survey (inventory, calibration) for this long when its scene is unchanged
    # (`planner.survey_cache`); 0 surveys every time. The probe and Rule CI always run.
    planner_survey_reuse_s: float = Field(default=1800.0, ge=0)
    # Proof portal (`compliance/`): signs share links; default a random key in <data>/compliance.
    rulecam_share_secret: SecretStr | None = None
    # Camera connectors (`rulecam.connectors`, `/connectors`): chunk rate, the widest frame
    # kept (wider streams are scaled down) and how many 3 s chunk files each camera keeps.
    rulecam_connectors_fps: float = Field(default=10.0, gt=0, le=30)
    rulecam_connectors_max_width: int = Field(default=1280, ge=320, le=3840)
    rulecam_connectors_keep_chunks: int = Field(default=1200, ge=10)
    # Smart glasses (`/wearables`): how long a look waits for Glimmer before Muse Spark looks.
    rulecam_wearables_glimmer_timeout_s: float = Field(default=25.0, gt=0.0, le=300.0)

    @property
    def replay_dir(self) -> Path:
        """Directory of the replay cache (overridable for tests)."""
        return self.rulecam_replay_dir or self.rulecam_data_dir / "replay"

    @property
    def spark_open_configured(self) -> bool:
        """Whether Muse Spark Open can be called live (an own/hosted server or a Baseten key)."""
        return bool(self.spark_open_base_url) or self.baseten_api_key is not None

    @property
    def regressions_dir(self) -> Path:
        """Directory of the site's permanent lookalike regressions."""
        return self.rulecam_regressions_dir or self.rulecam_data_dir / "rule_ci" / "regressions"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
