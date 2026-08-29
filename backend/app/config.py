from functools import lru_cache
import os
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT_DIR / "runtime"


def settings_env_files() -> tuple[Path, ...]:
    explicit_env_file = os.environ.get("PROSPER_ENV_FILE") or os.environ.get("WHATSAPP_PA_ENV_FILE", ".env")
    paths: list[Path] = []
    prod = ROOT_DIR / ".env.prod"
    if explicit_env_file != ".env.prod":
        paths.append(prod)
    paths.append(ROOT_DIR / explicit_env_file)
    return tuple(paths)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=settings_env_files(),
        extra="ignore",
    )

    database_url: str = f"sqlite:///{RUNTIME_DIR / 'whatsapp_pa.sqlite3'}"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-reasoner"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""
    langfuse_host: str = ""
    bridge_base_url: str = Field(
        default="http://127.0.0.1:8788",
        validation_alias=AliasChoices("PROSPER_BRIDGE_BASE_URL", "BRIDGE_BASE_URL"),
    )
    bridge_token: str = Field(
        default="",
        validation_alias=AliasChoices("PROSPER_BRIDGE_TOKEN", "WHATSAPP_PA_BRIDGE_TOKEN"),
    )
    auth_required: bool = False
    access_password: str = ""
    session_secret: str = ""
    session_ttl_seconds: int = 60 * 60 * 24
    auth_cookie_secure: bool = False
    media_root: Path = RUNTIME_DIR / "media"
    media_max_upload_bytes: int = 100 * 1024 * 1024
    seed_properties: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
