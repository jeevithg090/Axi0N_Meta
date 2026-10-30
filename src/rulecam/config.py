"""Application settings. This is the only module that reads environment variables (§5.3)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration, loaded from the environment and `.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    rulecam_mode: Literal["live", "record", "replay"] = "replay"
    rulecam_data_dir: Path = Path("./data")
    rulecam_replay_dir: Path | None = None

    meta_api_key: SecretStr | None = None
    meta_api_base_url: str | None = None
    baseten_api_key: SecretStr | None = None
    baseten_spark_open_model: str = "meta-models/muse-spark-open-1026"
    glimmer_base_url: str = "http://127.0.0.1:8080/v1"
    hf_token: SecretStr | None = None
    modal_token_id: SecretStr | None = None
    modal_token_secret: SecretStr | None = None
    kasa_plug_host: str | None = None
    kasa_light_host: str | None = None
    whatsapp_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    public_tunnel_url: str | None = None

    @property
    def replay_dir(self) -> Path:
        """Directory of the replay cache (overridable for tests)."""
        return self.rulecam_replay_dir or self.rulecam_data_dir / "replay"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
