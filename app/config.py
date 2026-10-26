"""Application configuration, loaded from environment / .env (pydantic-settings)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="sp_",
        extra="ignore",
        case_sensitive=False,
    )

    secret_key: str
    database_url: str
    env: str = "development"  # development | production | test
    host: str = "127.0.0.1"
    port: int = 8100
    public_base_url: str = "http://127.0.0.1:8100"

    # LLM / media provider defaults (used from Phase 2 onward; user-overridable).
    llm_provider: str = "openrouter"  # openrouter | openai_compatible | ollama | none
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    llm_model: str = "anthropic/claude-sonnet-4"
    # A local provider's endpoint, declared by the deployment (SP_LOCAL_LLM_URL). The outbound guard allows
    # a loopback address only when the deployment declares it here or it is the shipped default for the
    # provider — never because a *stored setting* says so, since that setting is written through the API by
    # the same admin the guard exists to constrain.
    local_llm_url: str = ""
    # The watermark key for AI-generated images (AI Act Article 50(2), app/ai_marking.py). Numeric. It is a
    # deployment value on purpose: key material in source is key material in a repository, unscoped per
    # installation. It must never change once content is published — a mark written with one key cannot be read
    # with another — so it is set once, here, and kept with the other secrets (never in the repository).
    ai_marking_key: str = ""
    llm_image_model: str = "google/gemini-2.5-flash-image"
    llm_video_model: str = "google/veo-3.1"
    comfyui_url: str = ""

    # Platform OAuth (used from Phase 2 onward).
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    instagram_client_id: str = ""
    instagram_client_secret: str = ""
    instagram_redirect_uri: str = ""
    facebook_client_id: str = ""
    facebook_client_secret: str = ""
    threads_client_id: str = ""
    threads_client_secret: str = ""
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    pinterest_client_id: str = ""
    pinterest_client_secret: str = ""
    x_client_id: str = ""
    x_client_secret: str = ""

    # Auto-backup.
    backup_dir: str = "./backups"
    backup_schedule: str = "03:00"

    # Multi-client management (Phase 7): telemetry API for the command center.
    # management_token authenticates the command center via X-Management-Token;
    # empty disables the API entirely. instance_label is a friendly name shown
    # on the command-center dashboard.
    management_token: str = ""
    instance_label: str = "Social Pilot"

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # populated from env/.env, not args


settings = get_settings()
