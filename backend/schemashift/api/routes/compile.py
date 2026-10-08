"""Stateless compile, persisted runs, export, samples and metrics."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select

from schemashift.api.db.models import Run
from schemashift.api.deps import get_state
from schemashift.api.runs import AppState
from schemashift.api.schemas import (
    CompileRequest,
    CompileResponse,
    MetricsResponse,
    RunCreated,
    RunDetail,
    RunPage,
    RunRequest,
    RunSummary,
    SampleDetail,
    SampleInfo,
)
from schemashift.api.service import RequestTooLarge, build_response, export_zip, run_compile
from schemashift.verification import VerificationReport

router = APIRouter()


def _detail(run: Run) -> RunDetail:
    return RunDetail(
        id=run.id,
        created_at=run.created_at,
        status=run.status,
        verify=run.verify,
        schema_sql=run.schema_sql,
        queries_sql=run.queries_sql,
        seed_sql=run.seed_sql,
        options=run.options,
        seed=run.seed,
        overall_verdict=run.overall_verdict,
        counts=run.counts,
        duration_ms=run.duration_ms,
        error=run.error,
        result=CompileResponse.model_validate(run.result) if run.result else None,
        verification=VerificationReport.model_validate(run.verification)
        if run.verification
        else None,
    )


@router.post("/compile", response_model=CompileResponse)
def compile_endpoint(req: CompileRequest, state: AppState = Depends(get_state)) -> CompileResponse:
    """Run every pure compiler stage. Nothing is stored and no SQL is executed."""
    try:
        result, codegen = run_compile(req, state.settings, state.advisor, state.cache)
    except RequestTooLarge as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return build_response(result, codegen, state.ai_enabled)


@router.post("/runs", response_model=RunCreated, status_code=202)
def create_run(req: RunRequest, state: AppState = Depends(get_state)) -> RunCreated:
    if req.verify and not state.settings.verification_enabled:
        raise HTTPException(status_code=503, detail="verification is not configured on this server")
    run_id = str(uuid.uuid4())
    with state.factory() as s:
        s.add(
            Run(
                id=run_id,
                status="pending",
                schema_sql=req.schema_sql,
                queries_sql=req.queries_sql,
                seed_sql=req.seed_sql,
                options=req.options.model_dump(mode="json"),
                seed=req.seed,
                verify=req.verify,
            )
        )
        s.commit()
    state.submit(run_id, req)
    return RunCreated(run_id=run_id)


@router.get("/runs", response_model=RunPage)
def list_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    state: AppState = Depends(get_state),
) -> RunPage:
    with state.factory() as s:
        total = s.scalar(select(func.count()).select_from(Run)) or 0
        rows = s.scalars(
            select(Run)
            .order_by(Run.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        items = [
            RunSummary(
                id=r.id,
                created_at=r.created_at,
                status=r.status,
                overall_verdict=r.overall_verdict,
                counts=r.counts,
                verify=r.verify,
                duration_ms=r.duration_ms,
                schema_preview=r.schema_sql.strip()[:120],
            )
            for r in rows
        ]
    return RunPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, state: AppState = Depends(get_state)) -> RunDetail:
    with state.factory() as s:
        run = s.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return _detail(run)


@router.get("/runs/{run_id}/export")
def export_run(
    run_id: str,
    format: str = Query("zip", pattern="^(zip|json)$"),
    state: AppState = Depends(get_state),
) -> Response:
    with state.factory() as s:
        run = s.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if run.status != "done":
            raise HTTPException(status_code=409, detail=f"run is {run.status}")
        detail = _detail(run).model_dump(mode="json")
    if format == "json":
        return Response(json.dumps(detail, indent=2), media_type="application/json")
    return Response(
        export_zip(run_id, detail),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="schemashift-{run_id[:8]}.zip"'},
    )


# ------------------------------------------------------------------------- samples
def _samples(state: AppState) -> dict[str, dict[str, Any]]:
    root = Path(state.settings.samples_dir)
    out: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "schema.sql").exists()):
        options_file = d / "options.json"
        options = json.loads(options_file.read_text("utf-8")) if options_file.exists() else {}
        out[d.name] = {
            "name": d.name,
            "description": options.pop("description", ""),
            "schema_sql": (d / "schema.sql").read_text("utf-8"),
            "queries_sql": (d / "queries.sql").read_text("utf-8")
            if (d / "queries.sql").exists()
            else "",
            "options": options,
        }
    return out


@router.get("/samples", response_model=list[SampleInfo])
def list_samples(state: AppState = Depends(get_state)) -> list[SampleInfo]:
    return [
        SampleInfo(name=s["name"], description=s["description"]) for s in _samples(state).values()
    ]


@router.get("/samples/{name}", response_model=SampleDetail)
def get_sample(name: str, state: AppState = Depends(get_state)) -> SampleDetail:
    sample = _samples(state).get(name)
    if sample is None:
        raise HTTPException(status_code=404, detail="sample not found")
    return SampleDetail.model_validate(sample)


@router.get("/metrics", response_model=MetricsResponse)
def metrics(state: AppState = Depends(get_state)) -> MetricsResponse:
    path = Path(state.settings.metrics_file)
    if not path.exists():
        return MetricsResponse(available=False)
    return MetricsResponse(available=True, data=json.loads(path.read_text("utf-8")))
