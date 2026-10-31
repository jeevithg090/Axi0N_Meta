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
    rulecam_sam_budget_frames_per_min: int = Field(default=1000, ge=0)  # per camera; 0 = no budget
    rulecam_sam_budget_stride: int = Field(default=3, ge=1)  # over budget: one chunk in N
    rulecam_runtime_concept_workers: int = Field(default=4, ge=1)  # concepts segmented at once
    rulecam_runtime_pipeline_depth: int = Field(default=4, ge=1)  # chunks per camera in flight
    # Rule ids whose owner turned operator exclusion off (comma-separated; rules/operators.py):
    # for these, a person riding a forklift counts as a pedestrian next to it.
    rulecam_operator_exclusion_off: str = ""
    # YuNet face-detector ONNX (opencv_zoo) for blurring faces in evidence strips; without it
    # only the head regions of tracked people are blurred (runtime.strips).
    rulecam_face_model: Path | None = None

    meta_api_key: SecretStr | None = None
    meta_api_base_url: str | None = None
    baseten_api_key: SecretStr | None = None
    baseten_base_url: str = "https://inference.baseten.co/v1"
    baseten_spark_open_model: str = "meta-models/muse-spark-open-1026"
    spark_open_images_supported: bool = False
    glimmer_base_url: str = "http://127.0.0.1:8080/v1"
    glimmer_api_key: SecretStr | None = None
    glimmer_model: str = "muse-glimmer-30b"
    glimmer_quant: str = ""
    glimmer_hardware: str = ""
    evidence_dir: Path = Path("./evidence")
    hf_token: SecretStr | None = None
    modal_token_id: SecretStr | None = None
    modal_token_secret: SecretStr | None = None
    sam_base_url: str | None = None  # our own GPU pod running sam_server; wins over Modal
    sam_api_key: SecretStr | None = None
    kasa_plug_host: str | None = None
    kasa_light_host: str | None = None
    whatsapp_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    public_tunnel_url: str | None = None
    public_ui_url: str | None = None  # public origin of the web UI (worker page QR on posters)
    planner_survey_window_s: float = (
        60.0  # seconds of recent chunks the planner samples frames from
    )

    @property
    def replay_dir(self) -> Path:
        """Directory of the replay cache (overridable for tests)."""
        return self.rulecam_replay_dir or self.rulecam_data_dir / "replay"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
