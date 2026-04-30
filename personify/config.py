from pathlib import Path
import re

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PERSONIFY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_url: str = "postgresql+psycopg://personify:personify@localhost:5544/personify"
    vault_dir: Path = Path("./vault")
    vault_name: str = "personal"
    vaults_dir: Path = Path("./vaults")
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embed_dim: int = 384

    @property
    def raw_dir(self) -> Path:
        return self.vault_dir / "raw"

    @property
    def staging_dir(self) -> Path:
        return self.vault_dir / "staging"

    @property
    def normalized_dir(self) -> Path:
        return self.vault_dir / "normalized"

    @property
    def manifests_dir(self) -> Path:
        return self.vault_dir / "manifests"

    @property
    def logs_dir(self) -> Path:
        return self.vault_dir / "logs"

    def all_dirs(self) -> list[Path]:
        return [self.raw_dir, self.staging_dir, self.normalized_dir, self.manifests_dir, self.logs_dir]


settings = Settings()


def normalize_vault_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise ValueError("vault name cannot be empty")
    return slug


def db_name_for_vault(name: str) -> str:
    slug = normalize_vault_name(name).replace("-", "_")
    return "personify" if slug == "personal" else f"personify_{slug}"


def db_url_for_vault(name: str, base_url: str | None = None) -> str:
    url = make_url(base_url or settings.db_url)
    return url.set(database=db_name_for_vault(name)).render_as_string(hide_password=False)


def vault_dir_for_name(name: str) -> Path:
    slug = normalize_vault_name(name)
    return Path("./vault") if slug == "personal" else settings.vaults_dir / slug


def configure_vault(name: str) -> None:
    """Point process-local settings at a named vault profile."""
    slug = normalize_vault_name(name)
    settings.vault_name = slug
    settings.db_url = db_url_for_vault(slug)
    settings.vault_dir = vault_dir_for_name(slug)


if normalize_vault_name(settings.vault_name) != "personal":
    configure_vault(settings.vault_name)
