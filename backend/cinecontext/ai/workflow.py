from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
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
        for _ in range(self.max_steps):
            if ctx.state in TERMINAL_STATES:
                return ctx
            node = self.nodes.get(ctx.state)
            if node is None:
                raise WorkflowRunnerError(f"no node registered for state={ctx.state.value}")
            ctx.outcome = None
            result = await node.run(ctx)
            if result is not ctx:
                raise WorkflowRunnerError(f"node {ctx.state.value} replaced the workflow context")
            if ctx.outcome is None:
                raise WorkflowRunnerError(f"node {ctx.state.value} did not set an outcome")
            ctx.state = self.transitions.next_state(ctx)
        raise WorkflowRunnerError(f"workflow exceeded step limit of {self.max_steps}")
