import logging

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
from ..settings import ToolsOptions
from .nodes import create_workflow
from .ports import ApplicationModelProvider
from .research_tools import create_research_tools
from .workflow import WorkflowContext, WorkflowRunner

logger = logging.getLogger(__name__)


class CurationService:
    def __init__(
        self,
        persistence: CurationPersistence,
        models: ApplicationModelProvider,
        tools: Options[ToolsOptions],
    ):
        self.persistence = persistence
        self.models = models
        self.tool_options = tools.value

    async def run(self, run_id, constraints: Constraints):
        model = None
        try:
            model = self.models.create()
            async with httpx.AsyncClient(timeout=self.tool_options.timeout_seconds) as http:
                tools = create_research_tools(self.persistence, http, self.tool_options)
                nodes, transitions = create_workflow(self.persistence)
                ctx = WorkflowContext(run_id, constraints, model, tools)
                await WorkflowRunner(nodes, transitions).run(ctx)
        except Exception as error:
            logger.error("Curation %s failed: %s", run_id, type(error).__name__)
            message = "The run failed. Check the model and tool connections before retrying."
            if isinstance(error, APITimeoutError):
                message = "The model timed out. You can run the research again."
            elif isinstance(error, RateLimitError):
                message = "The model service is rate limited. Try again shortly."
            elif isinstance(error, AuthenticationError):
                message = "Model authentication failed. Check the server API key."
            elif isinstance(error, APIConnectionError):
                message = "Cannot reach the model service. Try again shortly."
            await self.persistence.finish_run(run_id, RunStatus.FAILED, message)
        finally:
            if model:
                await model.close()
