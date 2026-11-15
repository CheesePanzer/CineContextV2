from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Request

from alcedo import ForbiddenError, Inject, ServiceScopeFactory
from alcedo import Options
from ..ai.ports import ApplicationModelProvider
from ..ai.types import ModelMode
from ..application.services import (
    FilmReadingsApplicationService, ParseApplicationService, RunApplicationService, TaccJobApplicationService,
)
from ..contracts import ParseRequest, RunRequest, TaccSubmissionRequest
from ..settings import ToolsOptions
from ..infrastructure.persistence import CurationJob

router = APIRouter()


def is_local(request: Request) -> bool:
    loopback = {"127.0.0.1", "::1"}
    return bool(request.client and request.client.host in loopback
                and request.url.hostname in loopback | {"localhost"})


@router.get("/health")
async def health(
    request: Request,
    models: Inject[ApplicationModelProvider],
    tools: Inject[Options[ToolsOptions]],
):
    readiness = await models.readiness() if models.mode == ModelMode.TACC else None
    job = None
    if readiness and readiness.job_id:
        job = {"id": readiness.job_id, "queued_seconds": readiness.queued_seconds,
               "remaining_seconds": readiness.remaining_seconds}
    return {"status": "ok", "model_mode": models.mode, "model_status": readiness.status if readiness else None,
            "tacc_job": job,
            "tacc_resubmit_enabled": bool(job and is_local(request)),
            "model_id": getattr(models, "model_id", None),
            "web_enabled": tools.value.brave.enabled or tools.value.wikipedia_enabled or tools.value.duckduckgo.enabled,
            "metadata_enabled": tools.value.tmdb.enabled,
            "ratings_enabled": tools.value.tmdb.enabled or tools.value.omdb.enabled,
            "tmdb_ratings_enabled": tools.value.tmdb.enabled,
            "omdb_ratings_enabled": tools.value.omdb.enabled}


@router.post("/tacc/jobs", status_code=202)
async def submit_tacc_job(
    body: TaccSubmissionRequest, request: Request, scope_factory: Inject[ServiceScopeFactory],
    models: Inject[ApplicationModelProvider],
):
    if not is_local(request):
        raise ForbiddenError("TACC jobs can be submitted from the local app.")
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise ForbiddenError("Submit the job from the CineContext page.")
    if models.mode != ModelMode.TACC:
        raise ForbiddenError("This instance does not manage TACC jobs.")
    async with scope_factory.create_scope() as scope:
        service = await scope.resolve(TaccJobApplicationService)
        return await service.submit(body.previous_job_id, restart=body.restart)


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


@router.get("/runs/{run_id}/films/{film_id}/readings")
async def get_film_readings(run_id: UUID, film_id: UUID, service: Inject[FilmReadingsApplicationService]):
    return await service.get(run_id, film_id)


@router.get("/films/{film_id}/ratings")
async def get_ratings(film_id: UUID, service: Inject[RunApplicationService]):
    return await service.get_ratings(film_id)
