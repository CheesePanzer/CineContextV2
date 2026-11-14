from uuid import UUID
from alcedo import ConflictError, NotFoundError, UnitOfWork, transactional
from openai import APIConnectionError, APITimeoutError, AuthenticationError, RateLimitError

from ..contracts import Constraints, ParseRequest, RunRequest
from ..ai.ports import ApplicationModelProvider
from ..ai.types import EvidenceRecord, FilmProjection
from ..ai.resume import GuideRetrySnapshot
from ..domain import CurationRun, RunStatus
from ..domain.repositories import FilmRepository, RunRepository
from .errors import ModelGatewayError, ModelGatewayTimeoutError

TERMINAL = {"completed", "partial", "failed", "interrupted"}


class ParseApplicationService:
    def __init__(self, models: ApplicationModelProvider):
        self.models = models

    async def parse(self, body: ParseRequest):
        model = None
        try:
            model = self.models.create()
            return await model.parse(body.prompt)
        except RateLimitError as error:
            from alcedo import TooManyRequestsError
            raise TooManyRequestsError("The model service is rate limited. Try again shortly.") from error
        except APITimeoutError as error:
            raise ModelGatewayTimeoutError("The model did not respond in time. Try again shortly.") from error
        except AuthenticationError as error:
            raise ModelGatewayError("Model authentication failed. Check the server API key.") from error
        except APIConnectionError as error:
            raise ModelGatewayError("Cannot reach the model service. Try again shortly.") from error
        except ValueError as error:
            from alcedo import BadRequestError
            raise BadRequestError("Could not parse the criteria. Check the dates or revise your question.") from error
        except Exception as error:
            raise ModelGatewayError("The model could not analyze the request.") from error
        finally:
            if model:
                await model.close()


class RunApplicationService:
    def __init__(
        self,
        runs: RunRepository,
        films: FilmRepository,
        uow: UnitOfWork,
        models: ApplicationModelProvider,
    ):
        self.runs = runs
        self.films = films
        self.uow = uow
        self.mode = models.mode

    async def list(self):
        rows = await self.runs.list_recent()
        return {"runs": [{"run_id": str(row.id), "topic": row.constraints["topic"],
                          "status": row.status, "created_at": row.created_at} for row in rows]}

    @transactional
    async def create(self, body: RunRequest):
        candidate = CurationRun.start(body.submission_key, body.constraints.model_dump(mode="json"), self.mode)
        new_id = await self.runs.create_once(candidate)
        if new_id:
            return new_id, "queued", True
        existing = await self.runs.find_by_submission_key(body.submission_key)
        if existing is None:
            raise RuntimeError("Submission conflict did not return the existing run.")
        if existing.constraints != candidate.constraints:
            raise ConflictError("This submission ID was already used with different criteria.")
        return existing.id, existing.status, False

    async def get(self, run_id: UUID):
        run = await self.runs.get(run_id)
        if not run:
            raise NotFoundError("Run not found.")
        study = run.study_result if run.status in TERMINAL else None
        if study and study.get("films"):
            films = study["films"]
            posters = await self.films.get_posters([UUID(film["id"]) for film in films])
            study = {**study, "films": [
                {**film, "poster_path": posters.get(film["id"], film.get("poster_path"))} for film in films
            ]}
        return {"run_id": str(run.id), "status": run.status, "stage": run.stage,
                "progress": run.progress, "updated_at": run.updated_at, "constraints": run.constraints,
                "model_mode": run.model_mode, "error": run.error, "study": study, "guide": run.guide}

    async def get_film(self, run_id: UUID, film_id: UUID):
        run = await self.runs.get(run_id)
        study = run.study_result if run else None
        film = next((item for item in (study or {}).get("films", []) if item["id"] == str(film_id)), None)
        if not film:
            raise NotFoundError("This film is outside the selected list.")
        guide = next((item for item in (run.guide or {}).get("film_guides", [])
                      if item["film_id"] == str(film_id)), None)
        return {"film": film, "guide": guide,
                "evidence": [item for item in study["evidence"] if item["film_id"] in {None, str(film_id)}]}

    async def get_ratings(self, film_id: UUID):
        if not await self.films.get(film_id):
            raise NotFoundError("Film not found.")
        rows = await self.films.get_ratings(film_id)
        return {"ratings": [{**row.payload, "origin": row.origin, "metric": row.metric} for row in rows]}

    @transactional
    async def delete(self, run_id: UUID) -> None:
        run = await self.runs.get_for_update(run_id)
        if run is None:
            raise NotFoundError("Run not found.")
        if RunStatus(run.status) not in {
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.FAILED,
            RunStatus.INTERRUPTED,
        }:
            raise ConflictError("An active research run cannot be deleted.")
        await self.runs.delete_with_data(run_id)

    @transactional
    async def retry_guide(self, run_id: UUID) -> GuideRetrySnapshot:
        run = await self.runs.get_for_update(run_id)
        if run is None:
            raise NotFoundError("Run not found.")
        if RunStatus(run.status) not in {
            RunStatus.PARTIAL,
            RunStatus.FAILED,
            RunStatus.INTERRUPTED,
        }:
            raise ConflictError("Only a terminal incomplete run can retry guide generation.")
        study = run.study_result or {}
        try:
            snapshot = GuideRetrySnapshot(
                constraints=Constraints.model_validate(run.constraints),
                films=[FilmProjection.model_validate(item) for item in study["films"]],
                evidence=[EvidenceRecord.model_validate(item) for item in study["evidence"]],
            )
        except (KeyError, ValueError) as error:
            raise ConflictError("This run has no persisted guide inputs.") from error
        if not snapshot.films or not snapshot.published_evidence():
            raise ConflictError("This run has no persisted guide inputs.")
        run.begin_guide_retry()
        return snapshot


class StartupService:
    def __init__(
        self,
        runs: RunRepository,
        uow: UnitOfWork,
    ):
        self.runs = runs
        self.uow = uow

    @transactional
    async def initialize(self):
        await self.runs.interrupt_active("The server restarted. You can submit the run again.")
