"""Vault profile discovery and lifecycle helpers used by the web UI.

A *vault* is a named profile that points at its own Postgres database and
its own filesystem root. The active vault is process-local — switching
means re-pointing the engine at a different DB. For a single-user local
app that's fine; the UI just reloads after a switch.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from personify.config import (
    configure_vault,
    db_name_for_vault,
    db_url_for_vault,
    normalize_vault_name,
    settings,
    vault_dir_for_name,
)
from personify.db import init_db, reset_engine
from personify.util.vault import ensure_vault_layout


def postgres_db_exists(name: str) -> bool:
    """Check whether the Postgres database for `name` exists."""
    url = make_url(db_url_for_vault(name))
    if not url.get_backend_name().startswith("postgresql"):
        return Path(url.database or "").exists()
    db_name = url.database
    if not db_name:
        return False
    admin_url = url.set(database="postgres")
    admin_engine = create_engine(
        admin_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    try:
        with admin_engine.connect() as conn:
            return bool(
                conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": db_name},
                ).scalar()
            )
    except Exception:
        return False
    finally:
        admin_engine.dispose()


def _vault_info(name: str) -> dict[str, Any]:
    slug = normalize_vault_name(name)
    vault_dir = vault_dir_for_name(slug)
    db_url = db_url_for_vault(slug)
    return {
        "name": slug,
        "active": slug == settings.vault_name,
        "db_name": db_name_for_vault(slug),
        "db_url": db_url,
        "vault_dir": str(vault_dir),
        "dir_exists": vault_dir.exists(),
        "db_exists": postgres_db_exists(slug),
    }


def discover_vaults() -> list[dict[str, Any]]:
    """Return every vault we can find on this host plus the active one."""
    names: set[str] = {"personal", settings.vault_name}
    if settings.vaults_dir.exists():
        for child in settings.vaults_dir.iterdir():
            if child.is_dir():
                try:
                    names.add(normalize_vault_name(child.name))
                except ValueError:
                    continue
    return sorted(
        (_vault_info(n) for n in names),
        key=lambda v: (not v["active"], v["name"]),
    )


def get_active_vault() -> dict[str, Any]:
    return _vault_info(settings.vault_name)


def create_vault(name: str, activate: bool = True) -> dict[str, Any]:
    """Initialize a new vault (Postgres DB + filesystem layout + schema).

    If activate=False, the original active vault is restored after init.
    """
    slug = normalize_vault_name(name)
    previous = settings.vault_name
    configure_vault(slug)
    reset_engine()
    ensure_vault_layout()
    init_db()
    if not activate and previous != slug:
        configure_vault(previous)
        reset_engine()
    return _vault_info(slug)


def activate_vault(name: str) -> dict[str, Any]:
    """Switch the running process to a different vault."""
    slug = normalize_vault_name(name)
    if not postgres_db_exists(slug):
        raise ValueError(
            f"Vault '{slug}' has no database yet. Create it first."
        )
    configure_vault(slug)
    reset_engine()
    return _vault_info(slug)
