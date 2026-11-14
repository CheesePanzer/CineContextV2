from uuid import UUID

from fastapi import APIRouter, BackgroundTasks

from alcedo import Inject, ServiceScopeFactory
from alcedo import Options
from ..ai.ports import ApplicationModelProvider
from ..application.services import ParseApplicationService, RunApplicationService
from ..contracts import ParseRequest, RunRequest
from ..settings import ToolsOptions
from ..infrastructure.persistence import CurationJob

router = APIRouter()


@router.get("/health")
async def health(
    models: Inject[ApplicationModelProvider],
    tools: Inject[Options[ToolsOptions]],
):
    return {"status": "ok", "model_mode": models.mode,
            "model_id": getattr(models, "model_id", None),
            "web_enabled": tools.value.brave.enabled or tools.value.duckduckgo.enabled,
            "metadata_enabled": tools.value.tmdb.enabled,
            "ratings_enabled": tools.value.omdb.enabled}


@router.get("/runs")
async def list_runs(service: Inject[RunApplicationService]):
    return await service.list()


@router.post("/requests/parse")
async def parse(body: ParseRequest, service: Inject[ParseApplicationService]):
    return await service.parse(body)


@router.post("/runs", status_code=202)
async def start(
    body: RunRequest,
    tasks: BackgroundTasks,
    service: Inject[RunApplicationService],
    scope_factory: Inject[ServiceScopeFactory],
):
    run_id, status, created = await service.create(body)
    if created:
        tasks.add_task(CurationJob(scope_factory).run, run_id, body.constraints)
    return {"run_id": str(run_id), "status": status}


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, service: Inject[RunApplicationService]):
    return await service.get(run_id)


@router.post("/runs/{run_id}/retry-guide", status_code=202)
async def retry_guide(
    run_id: UUID,
    tasks: BackgroundTasks,
    service: Inject[RunApplicationService],
    scope_factory: Inject[ServiceScopeFactory],
):
    snapshot = await service.retry_guide(run_id)
    tasks.add_task(CurationJob(scope_factory).retry_guide, run_id, snapshot)
    return {"run_id": str(run_id), "status": "running", "phase": "guide"}


@router.delete("/runs/{run_id}")
async def delete_run(run_id: UUID, service: Inject[RunApplicationService]):
    await service.delete(run_id)
    return {"deleted": True}


@router.get("/runs/{run_id}/films/{film_id}")
async def get_film(run_id: UUID, film_id: UUID, service: Inject[RunApplicationService]):
    return await service.get_film(run_id, film_id)


@router.get("/films/{film_id}/ratings")
async def get_ratings(film_id: UUID, service: Inject[RunApplicationService]):
    return await service.get_ratings(film_id)
