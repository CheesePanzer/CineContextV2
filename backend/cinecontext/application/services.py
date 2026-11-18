import logging
import re
from uuid import UUID
from alcedo import ConflictError, NotFoundError, ServiceScopeFactory, ServiceUnavailableError, UnitOfWork, transactional
from openai import APIConnectionError, APIError, APITimeoutError, AuthenticationError, RateLimitError

from ..contracts import Constraints, ParseRequest, RunRequest
from ..ai.ports import ApplicationModelProvider, ModelUnavailableError
from ..ai.timeline import history_date_key
from ..ai.types import EvidenceRecord, FilmProjection, film_title_key
from ..ai.resume import GuideRetrySnapshot
from ..domain import CurationRun, RunStatus
from ..domain.repositories import FilmRepository, RunRepository
from .errors import ModelGatewayError, ModelGatewayTimeoutError
from .ports import FilmReadings, ReferenceSearch, TaccJobControl

TERMINAL = {"completed", "partial", "failed", "interrupted"}
logger = logging.getLogger(__name__)


def reference_setting_query(prompt: str) -> str:
    reference = re.search(
        r"\b(?i:time period|period|setting|era|historical background)\s+(?i:of)\s+"
        r'(?:《([^》]+)》|“([^”]+)”|"([^"]+)"|([A-Z].*?)(?=[,;!?]|\.(?:\s|$)|$))',
        prompt,
    )
    if reference is None:
        return ""
    title = next(part.strip() for part in reference.groups() if part is not None)
    return f"{title} setting" if title else ""


def is_reference_placeholder(setting: str, references: list[str]) -> bool:
    if re.search(r"\b(?:time period|period|setting|era)\s+of\b", setting, re.I):
        return True
    setting_words = " ".join(re.findall(r"[^\W_]+", film_title_key(setting)))
    if not setting_words:
        return False
    for reference in references:
        query = " ".join(re.findall(r"[^\W_]+", film_title_key(reference)))
        if query == setting_words:
            return True
        if query.startswith(setting_words + " ") and re.fullmatch(
            r"(?:(?:game|film|movie|book|novel|story|historical|time|plot) )*(?:setting|period|era|background|synopsis)",
            query[len(setting_words) + 1:],
        ):
            return True
    return False


def reference_search_period(setting: str) -> str:
    years = list(re.finditer(r"(?<![\w,])(?:\d{3,4}|[1-9],\d{3})(?!\d|,\d)", setting))
    if len(years) != 1 or re.search(
        r"\b(?:century|circa|around|about|before|after|early|mid|late|BCE|BC)\b"
        r"|\bc\.\s*\d|\d[sS]\b|\d\s*[-–—/]\s*\d", setting, re.I,
    ):
        return setting
    year = years[0]
    value = int(year.group().replace(",", ""))
    if not 100 <= value <= 2099:
        return setting
    century, offset = divmod(value - 1, 100)
    century += 1
    suffix = "th" if 11 <= century % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(century % 10, "th")
    part = "early" if offset < 33 else "mid" if offset < 66 else "late"
    prefix = setting[:year.start()]
    if not prefix:
        part = part.capitalize()
    elif re.search(r"\bin\s+$", prefix, re.I):
        part = f"the {part}"
    period = f"{prefix}{part} {century}{suffix} century{setting[year.end():]}"
    return period if len(period) <= 120 else setting


class TaccJobApplicationService:
    def __init__(self, jobs: TaccJobControl):
        self.jobs = jobs

    async def submit(self, previous_job_id: str, *, restart: bool = False):
        return {"job_id": await self.jobs.submit_after(previous_job_id, restart=restart)}


class ParseApplicationService:
    def __init__(self, models: ApplicationModelProvider, references: ReferenceSearch):
        self.models = models
        self.references = references

    async def parse(self, body: ParseRequest):
        model = None
        try:
            model = self.models.create()
            parsed = await model.parse(body.prompt)
            # Only the source-backed lookup below may set a reference match.
            parsed.reference_match = None
            references = list(parsed.reference_queries)
            if not references and (query := reference_setting_query(body.prompt)):
                references.append(query)
            if references:
                sources = await self.references.search(references)
                if sources:
                    try:
                        parsed.reference_match = await model.resolve_reference(body.prompt, sources)
                    except (ModelUnavailableError, APIError, ValueError):
                        logger.info("Reference lookup could not resolve the work")
                titles = [*references, parsed.reference_match.title] if parsed.reference_match else references
                if is_reference_placeholder(parsed.constraints.story_period, titles):
                    parsed.constraints.story_period = ""
                if parsed.reference_match:
                    if not parsed.constraints.story_period.strip():
                        setting = parsed.reference_match.story_setting
                        if parsed.constraints.setting_match == "nearby":
                            setting = reference_search_period(setting)
                        parsed.constraints.story_period = setting
                    parsed.clarification = ""
                elif not parsed.constraints.story_period.strip():
                    parsed.clarification = (
                        "We couldn't verify the referenced work's setting. "
                        "You can enter it below, revise the title, or search by theme."
                    )
            return parsed
        except RateLimitError as error:
            from alcedo import TooManyRequestsError
            raise TooManyRequestsError("The model service is rate limited. Try again shortly.") from error
        except APITimeoutError as error:
            raise ModelGatewayTimeoutError("The model did not respond in time. Try again shortly.") from error
        except AuthenticationError as error:
            raise ModelGatewayError("Model authentication failed. Check the server API key.") from error
        except APIConnectionError as error:
            raise ModelGatewayError("Cannot reach the model service. Try again shortly.") from error
        except ModelUnavailableError as error:
            raise ServiceUnavailableError(str(error)) from error
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
        return {"runs": [{
            "run_id": str(row.id),
            "topic": row.constraints["topic"],
            "theme": row.constraints.get("theme", ""),
            "research_title": row.constraints.get("research_title", ""),
            "status": row.status,
            "created_at": row.created_at,
        } for row in rows]}

    @transactional
    async def create(self, body: RunRequest):
        candidate = CurationRun.start(body.submission_key, body.constraints.model_dump(mode="json"), self.mode)
        new_id = await self.runs.create_once(candidate)
        if new_id:
            return new_id, "queued", True
        existing = await self.runs.find_by_submission_key(body.submission_key)
        if existing is None:
            raise RuntimeError("Submission conflict did not return the existing run.")
        if Constraints.model_validate(existing.constraints) != body.constraints:
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
        views = (study or {}).get("timeline_views") or {}
        if views.get("history"):
            history = sorted(views["history"], key=lambda event: history_date_key(
                event["period"], event.get("sort_year"),
            ))
            study = {**study, "timeline_views": {**views, "history": history}}
        return {"run_id": str(run.id), "status": run.status, "stage": run.stage,
                "progress": run.progress, "updated_at": run.updated_at, "constraints": run.constraints,
                "model_mode": run.model_mode, "error": run.error, "study": study, "guide": run.guide}

    async def get_film(self, run_id: UUID, film_id: UUID):
        run = await self.runs.get(run_id)
        study = run.study_result if run else None
        film = next((item for item in (study or {}).get("films", []) if item["id"] == str(film_id)), None)
        if not film:
            raise NotFoundError("This film is outside the selected list.")
        poster = (await self.films.get_posters([film_id])).get(str(film_id))
        if poster:
            film = {**film, "poster_path": poster}
        guide = next((item for item in (run.guide or {}).get("film_guides", [])
                      if item["film_id"] == str(film_id)), None)
        return {"film": film, "guide": guide,
                "evidence": [item for item in study["evidence"] if item.get("film_id") in {None, str(film_id)}]}

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


class FilmReadingsApplicationService:
    def __init__(self, scope_factory: ServiceScopeFactory, readings: FilmReadings):
        self.scope_factory = scope_factory
        self.readings = readings

    async def get(self, run_id: UUID, film_id: UUID):
        async with self.scope_factory.create_scope() as scope:
            runs = await scope.resolve(RunApplicationService)
            detail = await runs.get_film(run_id, film_id)
            film = FilmProjection.model_validate(detail["film"])
        return {"readings": await self.readings.for_film(film)}
