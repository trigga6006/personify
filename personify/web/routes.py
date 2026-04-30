from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import Session, select

from personify.db import get_session
from personify.models import Account, IngestionRun, Item, RawExport
from personify.parsers import PARSERS
from personify.services.embed import embed_pending, embed_stats
from personify.services.ingest import (
    ingest_all_pending,
    ingest_export,
    ingest_source,
    reset_export,
)
from personify.services.register import register_export
from personify.services.repos import (
    register_repo_intake,
    register_result_payload,
    scan_repo_intake,
    scan_row_payload,
)
from personify.services.vaults import (
    activate_vault,
    create_vault,
    discover_vaults,
    get_active_vault,
)

router = APIRouter()

STATIC_DIR = Path(__file__).parent / "static"


@router.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/ui", status_code=307)


@router.get("/ui", include_in_schema=False)
def ui_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/parsers")
def list_parsers() -> list[dict[str, str]]:
    return [
        {"slug": slug, "version": cls.PARSER_VERSION, "label": slug.replace("_", " ").title()}
        for slug, cls in sorted(PARSERS.items())
    ]


@router.get("/api/accounts")
def list_accounts(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [
        {"handle": a.handle, "display_name": a.display_name}
        for a in s.exec(select(Account).order_by(Account.handle)).all()
    ]


@router.get("/api/exports")
def list_exports(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = list(s.exec(select(RawExport).order_by(RawExport.received_at.desc())).all())
    out: list[dict[str, Any]] = []
    for r in rows:
        runs = list(
            s.exec(
                select(IngestionRun)
                .where(IngestionRun.raw_export_id == r.id)
                .order_by(IngestionRun.started_at.desc())
            ).all()
        )
        latest = runs[0] if runs else None
        items_count = int(
            s.exec(select(func.count(Item.id)).where(Item.raw_export_id == r.id)).one() or 0
        )
        out.append(
            {
                "id": r.id,
                "source": r.source_slug,
                "account": r.account_handle,
                "stored_path": r.stored_path,
                "original_path": r.original_path,
                "size_bytes": r.size_bytes,
                "sha256": r.sha256,
                "received_at": r.received_at.isoformat() if r.received_at else None,
                "notes": r.notes,
                "items": items_count,
                "runs": len(runs),
                "latest_run": (
                    {
                        "id": latest.id,
                        "status": latest.status,
                        "items_seen": latest.items_seen,
                        "items_inserted": latest.items_inserted,
                        "items_skipped": latest.items_skipped,
                        "started_at": latest.started_at.isoformat() if latest.started_at else None,
                        "finished_at": latest.finished_at.isoformat() if latest.finished_at else None,
                        "error": latest.error,
                    }
                    if latest
                    else None
                ),
            }
        )
    return out


class RegisterExportRequest(BaseModel):
    source: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    account: str = Field(..., min_length=1)
    notes: Optional[str] = None


@router.post("/api/exports")
def post_register_export(req: RegisterExportRequest) -> dict[str, Any]:
    p = Path(req.path).expanduser()
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"path does not exist: {p}")
    try:
        raw = register_export(req.source, p, req.account, notes=req.notes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "id": raw.id,
        "source": raw.source_slug,
        "account": raw.account_handle,
        "sha256": raw.sha256,
        "size_bytes": raw.size_bytes,
        "received_at": raw.received_at.isoformat() if raw.received_at else None,
    }


class IngestRequest(BaseModel):
    export_id: Optional[int] = None
    source: Optional[str] = None
    all_pending: bool = False
    replace: bool = False


class RepoIntakeScanRequest(BaseModel):
    path: str = Field(..., min_length=1)
    recursive: bool = False


class RepoIntakeRegisterRequest(RepoIntakeScanRequest):
    account: str = Field(default="code-corpus", min_length=1)
    ingest: bool = False
    notes: Optional[str] = None


def _run_summary(run: IngestionRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "raw_export_id": run.raw_export_id,
        "status": run.status,
        "parser": run.parser_name,
        "parser_version": run.parser_version,
        "items_seen": run.items_seen,
        "items_inserted": run.items_inserted,
        "items_skipped": run.items_skipped,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error,
    }


@router.post("/api/ingest")
def post_ingest(req: IngestRequest) -> dict[str, Any]:
    if req.replace and req.export_id is None:
        raise HTTPException(status_code=400, detail="replace requires export_id")
    if req.all_pending:
        if req.replace:
            raise HTTPException(status_code=400, detail="replace cannot be used with all_pending")
        runs = ingest_all_pending()
        return {"runs": [_run_summary(r) for r in runs]}
    if req.export_id is None and req.source is None:
        raise HTTPException(
            status_code=400, detail="pass export_id, source, or all_pending=true"
        )
    if req.export_id is not None:
        try:
            if req.replace:
                reset_export(req.export_id)
            run = ingest_export(req.export_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"runs": [_run_summary(run)]}
    runs = ingest_source(req.source)  # type: ignore[arg-type]
    return {"runs": [_run_summary(r) for r in runs]}


@router.post("/api/repos/scan")
def post_scan_repos(req: RepoIntakeScanRequest) -> dict[str, Any]:
    p = Path(req.path).expanduser()
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail=f"directory does not exist: {p}")
    rows = scan_repo_intake(p, recursive=req.recursive)
    return {"repos": [scan_row_payload(r) for r in rows]}


@router.post("/api/repos/register")
def post_register_repos(req: RepoIntakeRegisterRequest) -> dict[str, Any]:
    p = Path(req.path).expanduser()
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail=f"directory does not exist: {p}")
    results = register_repo_intake(
        p,
        account_handle=req.account,
        recursive=req.recursive,
        ingest=req.ingest,
        notes=req.notes,
    )
    return {"results": [register_result_payload(r) for r in results]}


@router.post("/api/exports/{export_id}/reset")
def post_reset_export(export_id: int) -> dict[str, Any]:
    try:
        result = reset_export(export_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


class EmbedRequest(BaseModel):
    limit: int = 500


@router.post("/api/embed")
def post_embed(req: EmbedRequest) -> dict[str, Any]:
    try:
        count = embed_pending(limit=req.limit)
    except ImportError as e:
        raise HTTPException(
            status_code=501,
            detail=f"Embedding backend unavailable: {e}. Install personify[embeddings].",
        )
    return {"embedded": count}


@router.get("/api/embed/stats")
def get_embed_stats() -> dict[str, Any]:
    return embed_stats()


# ---- Vaults --------------------------------------------------------------

@router.get("/api/vaults")
def list_vaults() -> dict[str, Any]:
    return {"active": get_active_vault(), "vaults": discover_vaults()}


class CreateVaultRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    activate: bool = True


@router.post("/api/vaults")
def post_create_vault(req: CreateVaultRequest) -> dict[str, Any]:
    try:
        info = create_vault(req.name, activate=req.activate)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return info


@router.post("/api/vaults/{name}/activate")
def post_activate_vault(name: str) -> dict[str, Any]:
    try:
        return activate_vault(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/runs")
def list_runs(limit: int = 25, s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = list(
        s.exec(
            select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(limit)
        ).all()
    )
    return [_run_summary(r) for r in rows]


@router.get("/api/items")
def list_items(
    source: Optional[str] = None,
    account: Optional[str] = None,
    kind: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    s: Session = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(Item)
    count_stmt = select(func.count(Item.id))
    if source:
        stmt = stmt.where(Item.source_slug == source)
        count_stmt = count_stmt.where(Item.source_slug == source)
    if account:
        stmt = stmt.where(Item.account_handle == account)
        count_stmt = count_stmt.where(Item.account_handle == account)
    if kind:
        stmt = stmt.where(Item.kind == kind)
        count_stmt = count_stmt.where(Item.kind == kind)
    total = int(s.exec(count_stmt).one() or 0)
    stmt = stmt.order_by(Item.ts.desc().nullslast(), Item.id.desc()).offset(offset).limit(limit)
    rows = list(s.exec(stmt).all())
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": i.id,
                "source": i.source_slug,
                "account": i.account_handle,
                "kind": i.kind,
                "title": i.title,
                "ts": i.ts.isoformat() if i.ts else None,
            }
            for i in rows
        ],
    }
