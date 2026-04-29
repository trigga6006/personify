from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PERSONIFY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_url: str = "postgresql+psycopg://personify:personify@localhost:5544/personify"
    vault_dir: Path = Path("./vault")
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
