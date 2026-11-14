import logging
from time import monotonic
from uuid import uuid4

import httpx
from alcedo import Options
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

from ..application.ports import CurationPersistence
from ..contracts import Constraints
from ..domain import RunStatus
from ..observability import sanitize_message
from ..settings import LoggingOptions, ToolsOptions
from .nodes import create_guide_retry_workflow, create_workflow
from .ports import ApplicationModelProvider
from .research_tools import create_research_tools
from .resume import GuideRetrySnapshot
from .workflow import CurationState, WorkflowContext, WorkflowRunner

logger = logging.getLogger(__name__)


class CurationService:
    def __init__(
        self,
        persistence: CurationPersistence,
        models: ApplicationModelProvider,
        tools: Options[ToolsOptions],
        logging_options: Options[LoggingOptions],
    ):
        self.persistence = persistence
        self.models = models
        self.tool_options = tools.value
        self.logging_options = logging_options.value

    async def run(self, run_id, constraints: Constraints):
        model = None
        started = monotonic()
        provider_id = getattr(self.models, "model_id", None) or "default"
        log_context = {"run_id": str(run_id)}
        logger.info(
            "Curation started model_mode=%s provider_id=%s requested_count=%d",
            self.models.mode.value, provider_id, constraints.count, extra=log_context,
        )
        try:
            model = self.models.create()
            model.set_run_id(run_id)
            async with httpx.AsyncClient(timeout=self.tool_options.timeout_seconds) as http:
                tools = create_research_tools(self.persistence, http, self.tool_options)
                nodes, transitions = create_workflow(self.persistence)
                ctx = WorkflowContext(run_id, constraints, model, tools)
                result = await WorkflowRunner(nodes, transitions).run(ctx)
                logger.info(
                    "Curation completed terminal_state=%s elapsed_ms=%d films=%d evidence=%d "
                    "decisions=%d selected=%d",
                    result.state.value, int((monotonic() - started) * 1000), len(result.films),
                    len(result.evidence), len(result.decisions), len(result.selected),
                    extra={**log_context, "state": result.state.value},
                )
        except Exception as error:
            diagnostic_id = str(uuid4())
            failure_context = {**log_context, "diagnostic_id": diagnostic_id}
            logger.exception(
                "Curation failed error_type=%s error_message=%s elapsed_ms=%d",
                type(error).__name__, sanitize_message(error),
                int((monotonic() - started) * 1000), extra=failure_context,
            )
            message = "The run failed. Check the model and tool connections before retrying."
            if isinstance(error, APITimeoutError):
                message = "The model timed out. You can run the research again."
            elif isinstance(error, RateLimitError):
                message = "The model service is rate limited. Try again shortly."
            elif isinstance(error, AuthenticationError):
                message = "Model authentication failed. Check the server API key."
            elif isinstance(error, APIConnectionError):
                message = "Cannot reach the model service. Try again shortly."
            diagnostic = f"Diagnostic {diagnostic_id}"
            if self.logging_options.include_technical_failure_detail:
                diagnostic += f": {type(error).__name__}: {sanitize_message(error)}"
            persisted_message = f"{message} {diagnostic}"[:1000]
            try:
                await self.persistence.finish_run(run_id, RunStatus.FAILED, persisted_message)
            except Exception:
                logger.exception("Failed to persist curation failure", extra=failure_context)
                raise
        finally:
            if model:
                try:
                    await model.close()
                except Exception:
                    logger.exception("Failed to close curation model", extra=log_context)
                    raise

    async def retry_guide(self, run_id, snapshot: GuideRetrySnapshot):
        model = None
        started = monotonic()
        log_context = {"run_id": str(run_id), "state": CurationState.GUIDE.value}
        logger.info("Guide retry started selected=%d evidence=%d", len(snapshot.films),
                    len(snapshot.evidence), extra=log_context)
        try:
            model = self.models.create()
            model.set_run_id(run_id)
            async with httpx.AsyncClient(timeout=self.tool_options.timeout_seconds) as http:
                tools = create_research_tools(self.persistence, http, self.tool_options)
                nodes, transitions = create_guide_retry_workflow(self.persistence)
                ctx = WorkflowContext(
                    run_id,
                    snapshot.constraints,
                    model,
                    tools,
                    state=CurationState.GUIDE,
                    films=snapshot.films,
                    selected=snapshot.films,
                    evidence=snapshot.evidence,
                    published_evidence=snapshot.published_evidence(),
                )
                result = await WorkflowRunner(nodes, transitions).run(ctx)
                logger.info("Guide retry completed terminal_state=%s elapsed_ms=%d",
                            result.state.value, int((monotonic() - started) * 1000),
                            extra={**log_context, "state": result.state.value})
        except Exception as error:
            await self._persist_failure(run_id, error, started, log_context)
        finally:
            if model:
                try:
                    await model.close()
                except Exception:
                    logger.exception("Failed to close guide retry model", extra=log_context)
                    raise

    async def _persist_failure(self, run_id, error, started, log_context):
        diagnostic_id = str(uuid4())
        failure_context = {**log_context, "diagnostic_id": diagnostic_id}
        logger.exception(
            "Guide retry failed error_type=%s error_message=%s elapsed_ms=%d",
            type(error).__name__, sanitize_message(error),
            int((monotonic() - started) * 1000), extra=failure_context,
        )
        message = "Guide generation failed. You can retry this phase again."
        diagnostic = f"Diagnostic {diagnostic_id}"
        if self.logging_options.include_technical_failure_detail:
            diagnostic += f": {type(error).__name__}: {sanitize_message(error)}"
        try:
            await self.persistence.finish_run(
                run_id, RunStatus.FAILED, f"{message} {diagnostic}"[:1000]
            )
        except Exception:
            logger.exception("Failed to persist guide retry failure", extra=failure_context)
            raise
