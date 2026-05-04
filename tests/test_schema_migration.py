"""Verify that init_db() backfills column-level schema additions on existing DBs.

The user's live vault was created before `entities.origin` /
`relationships.origin` / `pipeline_stages` existed. SQLAlchemy's create_all
adds new tables but never alters existing ones, so init_db() applies an
idempotent ALTER TABLE pass for the column additions.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text


def _bootstrap_legacy_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "personify.db"
    monkeypatch.setenv("PERSONIFY_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PERSONIFY_VAULT_DIR", str(tmp_path / "vault"))

    import personify.config as config
    import personify.db as db
    import personify.util.vault as vault

    config.settings = config.Settings()
    vault.settings = config.settings
    db.engine = db.create_engine(config.settings.db_url, echo=False, pool_pre_ping=True)
    vault.ensure_vault_layout()
    return db


def test_init_db_adds_origin_column_to_legacy_entities_table(tmp_path: Path, monkeypatch) -> None:
    db = _bootstrap_legacy_db(tmp_path, monkeypatch)

    # Simulate the pre-migration shape: entities and relationships exist without
    # `origin`, and `pipeline_stages` does not exist.
    with db.get_engine().begin() as conn:
        conn.execute(text("""
            CREATE TABLE entities (
                id INTEGER PRIMARY KEY,
                database_id VARCHAR(128),
                type VARCHAR(64) NOT NULL,
                name TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                description TEXT,
                metadata JSON,
                source_count INTEGER DEFAULT 0,
                confidence NUMERIC(4,3),
                created_at DATETIME,
                updated_at DATETIME
            )
        """))
        conn.execute(text("""
            CREATE TABLE relationships (
                id INTEGER PRIMARY KEY,
                database_id VARCHAR(128),
                source_entity_id INTEGER NOT NULL,
                target_entity_id INTEGER NOT NULL,
                relationship_type VARCHAR(64) NOT NULL,
                confidence NUMERIC(4,3),
                metadata JSON,
                created_at DATETIME,
                updated_at DATETIME
            )
        """))
        conn.execute(text("INSERT INTO entities (type, name, canonical_name) VALUES ('Project', 'Legacy', 'legacy')"))

    pre = inspect(db.get_engine())
    assert "origin" not in {c["name"] for c in pre.get_columns("entities")}
    assert "origin" not in {c["name"] for c in pre.get_columns("relationships")}
    assert "pipeline_stages" not in pre.get_table_names()

    db.init_db()

    post = inspect(db.get_engine())
    assert "origin" in {c["name"] for c in post.get_columns("entities")}
    assert "origin" in {c["name"] for c in post.get_columns("relationships")}
    assert "pipeline_stages" in post.get_table_names()

    # The pre-existing row gets the default origin so the reset_export prune
    # logic treats it as manual (i.e. preserved).
    with db.get_engine().begin() as conn:
        origin = conn.execute(text("SELECT origin FROM entities WHERE name = 'Legacy'")).scalar()
    assert origin == "manual"


def test_init_db_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    """Running init_db() repeatedly must not error or duplicate columns."""
    db = _bootstrap_legacy_db(tmp_path, monkeypatch)
    db.init_db()
    db.init_db()  # second pass is a no-op, not a re-add

    insp = inspect(db.get_engine())
    entity_origin_cols = [c for c in insp.get_columns("entities") if c["name"] == "origin"]
    assert len(entity_origin_cols) == 1
