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
    # Context for `POST /ask` (the owner's words, given to the models as they are): what the site
    # is, and camera id -> name pairs ("phone-62=checkout lanes,phone-61=entrance queue").
    rulecam_site_profile: str = ""
    rulecam_camera_names: str = ""
    # Ask's temporal memory: Glimmer captions each camera every N seconds of video; 0 = off.
    rulecam_ask_caption_every_s: float = Field(default=0.0, ge=0.0)
    # Ask's identity memory: join track fragments by DINOv2 appearance on the GPU pod's /embed.
    rulecam_ask_identities: bool = True
    # Evidence strips sent to Glimmer: frames per strip and their width in pixels.
    rulecam_strip_frames: int = Field(default=6, ge=1, le=12)
    rulecam_strip_max_width: int = Field(default=640, ge=320, le=1920)
    # RTMPose-l body7 ONNX (`scripts/fetch_rtmpose.sh`; runs on the CPU, needs `onnxruntime`):
    # strips then draw each person's real pose instead of a figure fitted to the box. Unset, or
    # a file whose sha256 is not RULECAM_POSE_SHA256 (pose.keypoints.RTMPOSE_BODY7_ONNX_SHA256),
    # means no pose.
    rulecam_pose_onnx: Path | None = None
    rulecam_pose_sha256: str = "cff059fd58a2c0d5fabaddcd66a96abcfb327563bcb0149ea59c9de4a8990fe2"

    meta_api_key: SecretStr | None = None
    meta_api_base_url: str | None = None
    baseten_api_key: SecretStr | None = None
    baseten_base_url: str = "https://inference.baseten.co/v1"
    baseten_spark_open_model: str = "meta-models/muse-spark-open-1026"
    spark_open_images_supported: bool = False
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
    evidence_dir: Path = Path("./evidence")
    hf_token: SecretStr | None = None
    modal_token_id: SecretStr | None = None
    modal_token_secret: SecretStr | None = None
    sam_base_url: str | None = None  # our own GPU pod running sam_server; wins over Modal
    sam_api_key: SecretStr | None = None
    # SAM_BASE_URL is a RunPod serverless load balancer: its edge takes this RunPod API key as
    # `Authorization`, and SAM_API_KEY goes in `X-Rulecam-Key` to the worker.
    sam_edge_key: SecretStr | None = None
    kasa_plug_host: str | None = None
    kasa_light_host: str | None = None
    whatsapp_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_app_secret: SecretStr | None = None  # verifies X-Hub-Signature-256 on the webhook
    whatsapp_verify_token: SecretStr | None = None  # the webhook subscription handshake
    # role=E.164 pairs, e.g. "aide=15551234567,charge_nurse=15557654321" (kept only in .env)
    whatsapp_recipients: SecretStr | None = None
    whatsapp_api_base: str = "https://graph.facebook.com/v21.0"
    public_tunnel_url: str | None = None
    public_ui_url: str | None = None  # public origin of the web UI (worker page QR on posters)
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

    @property
    def replay_dir(self) -> Path:
        """Directory of the replay cache (overridable for tests)."""
        return self.rulecam_replay_dir or self.rulecam_data_dir / "replay"

    @property
    def regressions_dir(self) -> Path:
        """Directory of the site's permanent lookalike regressions."""
        return self.rulecam_regressions_dir or self.rulecam_data_dir / "rule_ci" / "regressions"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
