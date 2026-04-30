from pathlib import Path

from personify.db import init_db
from personify.services.ingest import ingest_export, reset_export
from personify.services.register import register_export
from personify.services.stats import collect_stats


def test_reset_export_removes_derived_rows(
    tmp_path: Path,
    monkeypatch,
    fixtures_dir: Path,
) -> None:
    db_path = tmp_path / "personify.db"
    monkeypatch.setenv("PERSONIFY_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PERSONIFY_VAULT_DIR", str(tmp_path / "vault"))

    import personify.config as config
    import personify.db as db
    import personify.services.register as register
    import personify.util.vault as vault

    config.settings = config.Settings()
    vault.settings = config.settings
    db.engine = db.create_engine(config.settings.db_url, echo=False, pool_pre_ping=True)
    register.ensure_vault_layout()
    init_db()

    raw = register_export("files", fixtures_dir / "files", "test")
    ingest_export(raw.id)
    assert collect_stats()["items"] == 2

    deleted = reset_export(raw.id)

    assert deleted == {"items": 2, "runs": 1}
    assert collect_stats()["items"] == 0
