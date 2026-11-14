from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import logging
from time import monotonic
from typing import Protocol
from uuid import UUID

from ..contracts import Constraints, Guide, Plan
from .ports import ApplicationModel
from .research_tools import ResearchTools
from .types import (
    EvidenceRecord,
    FilmProjection,
    FollowUpKey,
    FollowUpRequest,
    StudyPayload,
    ValidatedDecision,
)

logger = logging.getLogger(__name__)

MAX_FOLLOW_UP_QUERIES = 4
MAX_FOLLOW_UPS_PER_FILM = 2
MAX_GUIDE_ATTEMPTS = 2
MAX_WORKFLOW_STEPS = 32

class CurationState(StrEnum):
    PLAN = "plan"
    DISCOVER = "discover"
    ASSESS = "assess"
    FOLLOW_UP = "follow_up"
    SELECT = "select"
    TIMELINE = "timeline"
    GUIDE = "guide"
    RATINGS = "ratings"
    PUBLISH = "publish"
    PUBLISH_PARTIAL = "publish_partial"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_STATES = frozenset({CurationState.COMPLETED, CurationState.FAILED})


class NodeOutcome(StrEnum):
    SUCCESS = "success"
    FOLLOW_UP = "follow_up"
    RETRY = "retry"
    PARTIAL = "partial"


@dataclass
class WorkflowContext:
    run_id: UUID
    constraints: Constraints
    model: ApplicationModel
    tools: ResearchTools
    state: CurationState = CurationState.PLAN
    outcome: NodeOutcome | None = None
    plan: Plan | None = None
    films: list[FilmProjection] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    decisions: dict[UUID, ValidatedDecision] = field(default_factory=dict)
    selected: list[FilmProjection] = field(default_factory=list)
    published_evidence: list[EvidenceRecord] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    follow_up_attempts: dict[UUID, int] = field(default_factory=lambda: defaultdict(int))
    seen_follow_up_queries: set[FollowUpKey] = field(default_factory=set)
    pending_follow_up: FollowUpRequest | None = None
    assessment_films: list[FilmProjection] | None = None
    study: StudyPayload | None = None
    guide: Guide | None = None
    guide_attempts: int = 0
    guide_correction: str = ""
    partial_message: str = "The guide is incomplete. The sources and timeline were saved."

    def require_plan(self) -> Plan:
        if self.plan is None:
            raise RuntimeError("workflow plan is not available in this state")
        return self.plan

    def require_study(self) -> StudyPayload:
        if self.study is None:
            raise RuntimeError("study payload is not available in this state")
        return self.study

    def require_guide(self) -> Guide:
        if self.guide is None:
            raise RuntimeError("guide is not available in this state")
        return self.guide


@dataclass(frozen=True)
class GuideRetrySnapshot:
    constraints: Constraints
    films: list[FilmProjection]
    evidence: list[EvidenceRecord]

    def published_evidence(self) -> list[EvidenceRecord]:
        film_ids = {film.id for film in self.films}
        return [
            item for item in self.evidence
            if item.film_id is None or item.film_id in film_ids
        ]


class WorkflowNode(Protocol):
    async def run(self, ctx: WorkflowContext) -> WorkflowContext: ...


Guard = Callable[[WorkflowContext], bool]


@dataclass(frozen=True)
class WorkflowTransition:
    source: CurationState
    outcome: NodeOutcome
    target: CurationState
    guard: Guard | None = None


class WorkflowTransitionError(RuntimeError):
    pass


class WorkflowRunnerError(RuntimeError):
    pass


class TransitionEngine:
    def __init__(self, transitions: Sequence[WorkflowTransition]):
        self.transitions = tuple(transitions)

    def next_state(self, ctx: WorkflowContext) -> CurationState:
        matches = [
            rule for rule in self.transitions
            if rule.source == ctx.state and rule.outcome == ctx.outcome
            and (rule.guard is None or rule.guard(ctx))
        ]
        if len(matches) != 1:
            detail = "no" if not matches else "ambiguous"
            raise WorkflowTransitionError(
                f"{detail} transition for state={ctx.state.value}, outcome="
                f"{ctx.outcome.value if ctx.outcome else None}; matched {len(matches)} rules"
            )
        return matches[0].target


class WorkflowRunner:
    def __init__(
        self,
        nodes: Mapping[CurationState, WorkflowNode],
        transitions: TransitionEngine,
        max_steps: int = MAX_WORKFLOW_STEPS,
    ):
        self.nodes = dict(nodes)
        self.transitions = transitions
        self.max_steps = max_steps

    async def run(self, ctx: WorkflowContext) -> WorkflowContext:
        workflow_started = monotonic()
        logger.debug("Workflow started", extra=self._context(ctx))
        for step in range(1, self.max_steps + 1):
            if ctx.state in TERMINAL_STATES:
                logger.info(
                    "Workflow completed elapsed_ms=%d films=%d evidence=%d decisions=%d selected=%d steps=%d",
                    int((monotonic() - workflow_started) * 1000), len(ctx.films), len(ctx.evidence),
                    len(ctx.decisions), len(ctx.selected), step - 1, extra=self._context(ctx),
                )
                return ctx
            node = self.nodes.get(ctx.state)
            if node is None:
                raise WorkflowRunnerError(f"no node registered for state={ctx.state.value}")
            state = ctx.state
            node_name = type(node).__name__
            started = monotonic()
            logger.debug("Node started node=%s step=%d", node_name, step, extra=self._context(ctx))
            ctx.outcome = None
            try:
                result = await node.run(ctx)
                if result is not ctx:
                    raise WorkflowRunnerError(f"node {state.value} replaced the workflow context")
                if ctx.outcome is None:
                    raise WorkflowRunnerError(f"node {state.value} did not set an outcome")
                next_state = self.transitions.next_state(ctx)
            except Exception as error:
                logger.error(
                    "Node failed node=%s step=%d error_type=%s elapsed_ms=%d",
                    node_name, step, type(error).__name__,
                    int((monotonic() - started) * 1000), extra=self._context(ctx),
                )
                raise
            logger.debug(
                "Node completed node=%s step=%d outcome=%s next_state=%s elapsed_ms=%d "
                "films=%d evidence=%d decisions=%d selected=%d follow_up_attempts=%d guide_attempts=%d",
                node_name, step, ctx.outcome.value, next_state.value,
                int((monotonic() - started) * 1000), len(ctx.films), len(ctx.evidence),
                len(ctx.decisions), len(ctx.selected), sum(ctx.follow_up_attempts.values()),
                ctx.guide_attempts, extra=self._context(ctx),
            )
            ctx.state = next_state
        raise WorkflowRunnerError(f"workflow exceeded step limit of {self.max_steps}")

    @staticmethod
    def _context(ctx: WorkflowContext) -> dict:
        return {"run_id": str(ctx.run_id), "state": ctx.state.value}
