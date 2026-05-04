"""Pipeline orchestration: ingest → optional embeddings → optional graph extraction.

The standard ingest is the trusted base path: every pipeline run executes it.
Embeddings and graph extraction are independently toggleable follow-on stages,
each tracked as a `PipelineStage` row so the UI can render per-stage status.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlmodel import select

from personify.db import session_scope
from personify.models import IngestionRun, PipelineStage, RawExport
from personify.services.ingest import ingest_export

STAGE_INGEST = "ingest"
STAGE_EMBED = "embed"
STAGE_GRAPH = "graph"

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"


@dataclass
class StageResult:
    stage: str
    status: str
    items_processed: int = 0
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    pipeline_stage_id: Optional[int] = None


@dataclass
class PipelineResult:
    raw_export_id: int
    ingestion_run_id: Optional[int]
    stages: list[StageResult]

    def stage(self, name: str) -> Optional[StageResult]:
        return next((s for s in self.stages if s.stage == name), None)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _create_stage(raw_export_id: int, stage: str) -> int:
    with session_scope() as s:
        row = PipelineStage(raw_export_id=raw_export_id, stage=stage, status=STATUS_PENDING)
        s.add(row)
        s.flush()
        return row.id


def _update_stage(
    stage_id: int,
    *,
    status: Optional[str] = None,
    items_processed: Optional[int] = None,
    error: Optional[str] = None,
    metadata: Optional[dict] = None,
    started: bool = False,
    finished: bool = False,
    ingestion_run_id: Optional[int] = None,
) -> None:
    with session_scope() as s:
        row = s.get(PipelineStage, stage_id)
        if not row:
            return
        if status is not None:
            row.status = status
        if items_processed is not None:
            row.items_processed = items_processed
        if error is not None:
            row.error = error
        if metadata is not None:
            row.metadata_json = {**(row.metadata_json or {}), **metadata}
        if started:
            row.started_at = _utcnow()
        if finished:
            row.finished_at = _utcnow()
        if ingestion_run_id is not None:
            row.ingestion_run_id = ingestion_run_id


@contextmanager
def _run_stage(stage: str, raw_export_id: int) -> Iterator[StageResult]:
    """Wrap a stage so its PipelineStage row tracks pending → running → done/error.

    Exceptions raised inside the with-block are recorded on the stage row and
    swallowed: the pipeline as a whole continues so downstream stages can be
    marked skipped, and callers branch on `result.status` instead of catching.
    """
    stage_id = _create_stage(raw_export_id, stage)
    result = StageResult(stage=stage, status=STATUS_PENDING, pipeline_stage_id=stage_id)
    _update_stage(stage_id, status=STATUS_RUNNING, started=True)
    result.status = STATUS_RUNNING
    try:
        yield result
    except Exception as e:  # noqa: BLE001
        msg = repr(e)
        _update_stage(stage_id, status=STATUS_ERROR, error=msg, finished=True)
        result.status = STATUS_ERROR
        result.error = msg
        return
    # Stage code is responsible for setting result.status to done/skipped
    # plus items_processed and metadata; we persist the final view here.
    if result.status == STATUS_RUNNING:
        result.status = STATUS_DONE
    _update_stage(
        stage_id,
        status=result.status,
        items_processed=result.items_processed,
        metadata=result.metadata or None,
        finished=True,
    )


def _record_skipped(stage: str, raw_export_id: int) -> StageResult:
    stage_id = _create_stage(raw_export_id, stage)
    _update_stage(stage_id, status=STATUS_SKIPPED, finished=True)
    return StageResult(stage=stage, status=STATUS_SKIPPED, pipeline_stage_id=stage_id)


def run_pipeline(
    raw_export_id: int,
    *,
    with_embeddings: bool = False,
    with_graph: bool = False,
) -> PipelineResult:
    """Execute the standard pipeline for a single raw export.

    Standard ingest is always run. Embeddings and graph extraction are opt-in.
    Each stage is tracked as a `PipelineStage` row keyed by raw_export_id.
    """
    with session_scope() as s:
        if not s.get(RawExport, raw_export_id):
            raise ValueError(f"raw_export {raw_export_id} not found")

    stages: list[StageResult] = []
    ingestion_run_id: Optional[int] = None

    # --- Stage 1: standard ingest ---------------------------------------
    with _run_stage(STAGE_INGEST, raw_export_id) as ingest_result:
        run = ingest_export(raw_export_id)
        ingestion_run_id = run.id
        ingest_result.items_processed = run.items_inserted
        ingest_result.metadata = {
            "items_seen": run.items_seen,
            "items_inserted": run.items_inserted,
            "items_skipped": run.items_skipped,
            "parser": run.parser_name,
            "parser_version": run.parser_version,
        }
        if run.status != "ok":
            ingest_result.status = STATUS_ERROR
            ingest_result.error = run.error or "ingest_export reported non-ok status"
        if ingestion_run_id is not None:
            _update_stage(ingest_result.pipeline_stage_id, ingestion_run_id=ingestion_run_id)

    # If ingest_export raised, the with-block exited before assigning
    # ingestion_run_id. ingest_export has already committed an IngestionRun
    # with status='error' for real failures — link the stage to it so the UI
    # can navigate from the failed stage to the run/error record.
    if ingest_result.status == STATUS_ERROR and ingestion_run_id is None:
        with session_scope() as s:
            latest = s.exec(
                select(IngestionRun)
                .where(IngestionRun.raw_export_id == raw_export_id)
                .order_by(IngestionRun.id.desc())
            ).first()
            if latest is not None:
                ingestion_run_id = latest.id
                _update_stage(ingest_result.pipeline_stage_id, ingestion_run_id=latest.id)
    stages.append(ingest_result)

    # If ingest failed, don't run downstream stages — record them as skipped.
    if ingest_result.status != STATUS_DONE:
        if with_embeddings:
            stages.append(_record_skipped(STAGE_EMBED, raw_export_id))
        if with_graph:
            stages.append(_record_skipped(STAGE_GRAPH, raw_export_id))
        return PipelineResult(raw_export_id=raw_export_id, ingestion_run_id=ingestion_run_id, stages=stages)

    # --- Stage 2: embeddings (optional) ---------------------------------
    if with_embeddings:
        try:
            from personify.services.embed import embed_export
        except ImportError as e:  # pragma: no cover - defensive
            stages.append(_record_skipped(STAGE_EMBED, raw_export_id))
            stages[-1].metadata = {"reason": f"import error: {e}"}
        else:
            with _run_stage(STAGE_EMBED, raw_export_id) as embed_result:
                if ingestion_run_id is not None:
                    _update_stage(embed_result.pipeline_stage_id, ingestion_run_id=ingestion_run_id)
                try:
                    chunks = embed_export(raw_export_id)
                    embed_result.items_processed = chunks
                    embed_result.metadata = {"chunks": chunks}
                except ImportError as e:
                    embed_result.status = STATUS_SKIPPED
                    embed_result.metadata = {"reason": f"embedding backend unavailable: {e}"}
            stages.append(embed_result)

    # --- Stage 3: graph extraction (optional) ---------------------------
    if with_graph:
        from personify.services.graph_extractor import extract_graph_for_export

        with _run_stage(STAGE_GRAPH, raw_export_id) as graph_result:
            if ingestion_run_id is not None:
                _update_stage(graph_result.pipeline_stage_id, ingestion_run_id=ingestion_run_id)
            summary = extract_graph_for_export(raw_export_id)
            graph_result.items_processed = summary["entities_created"]
            graph_result.metadata = summary
        stages.append(graph_result)

    return PipelineResult(raw_export_id=raw_export_id, ingestion_run_id=ingestion_run_id, stages=stages)


def latest_stages_for_export(raw_export_id: int) -> dict[str, PipelineStage]:
    """Return the most recent PipelineStage per stage name for a raw export."""
    with session_scope() as s:
        rows = list(
            s.exec(
                select(PipelineStage)
                .where(PipelineStage.raw_export_id == raw_export_id)
                .order_by(PipelineStage.id.desc())
            ).all()
        )
    out: dict[str, PipelineStage] = {}
    for r in rows:
        out.setdefault(r.stage, r)
    return out


def stage_summary(stage: PipelineStage) -> dict:
    return {
        "id": stage.id,
        "raw_export_id": stage.raw_export_id,
        "ingestion_run_id": stage.ingestion_run_id,
        "stage": stage.stage,
        "status": stage.status,
        "started_at": stage.started_at.isoformat() if stage.started_at else None,
        "finished_at": stage.finished_at.isoformat() if stage.finished_at else None,
        "items_processed": stage.items_processed,
        "error": stage.error,
        "metadata": stage.metadata_json or {},
    }


def pipeline_result_summary(result: PipelineResult) -> dict:
    return {
        "raw_export_id": result.raw_export_id,
        "ingestion_run_id": result.ingestion_run_id,
        "stages": [
            {
                "stage": s.stage,
                "status": s.status,
                "items_processed": s.items_processed,
                "error": s.error,
                "metadata": s.metadata,
            }
            for s in result.stages
        ],
    }


def latest_pipeline_summary(raw_export_id: int) -> dict:
    """Convenience: render `latest_stages_for_export` as a UI-ready dict."""
    stages = latest_stages_for_export(raw_export_id)
    return {
        "raw_export_id": raw_export_id,
        "stages": {name: stage_summary(s) for name, s in stages.items()},
    }


def reset_pipeline_stages(raw_export_id: int) -> int:
    """Delete all PipelineStage rows for an export. Used by reset_export hooks/tests."""
    with session_scope() as s:
        rows = list(
            s.exec(
                select(PipelineStage).where(PipelineStage.raw_export_id == raw_export_id)
            ).all()
        )
        for r in rows:
            s.delete(r)
        return len(rows)
