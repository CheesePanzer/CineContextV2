import asyncio
import json
import logging
from collections import defaultdict
from time import monotonic
from uuid import UUID

import httpx
from openai import APIError, APITimeoutError, RateLimitError
from pydantic import ValidationError

from ..application.ports import CurationPersistence
from ..contracts import FilmAssessment, TimelineResearch
from ..domain import RunStatus
from ..observability import sanitize_message
from .guide_validation import GuideValidator
from .timeline import TimelineBuilder
from .types import (
    DecisionStatus,
    EvidenceDraft,
    EvidenceKind,
    EvidenceRecord,
    FollowUpRequest,
    OperationName,
    ProgressStage,
    Relevance,
    StudyDecision,
    StudyPayload,
    ToolCallStatus,
    ValidatedDecision,
)

logger = logging.getLogger(__name__)
from .workflow import (
    MAX_FOLLOW_UP_QUERIES,
    MAX_FOLLOW_UPS_PER_FILM,
    MAX_GUIDE_ATTEMPTS,
    CurationState,
    NodeOutcome,
    TransitionEngine,
    WorkflowContext,
    WorkflowTransition,
)


class WorkflowOperations:
    def __init__(self, persistence: CurationPersistence):
        self.persistence = persistence

    async def logged(self, ctx, operation: OperationName, action, detail=None):
        started = monotonic()
        log_context = self._context(ctx, operation)
        logger.debug("Operation started", extra=log_context)
        try:
            result = await action()
        except Exception as error:
            error_detail = {
                **(detail or {}),
                "error_type": type(error).__name__,
                "error_message": sanitize_message(error),
                "state": ctx.state.value,
            }
            logger.error(
                "Operation failed error_type=%s error_message=%s elapsed_ms=%d",
                type(error).__name__, sanitize_message(error),
                int((monotonic() - started) * 1000), extra=log_context,
            )
            await self._record(ctx, operation, ToolCallStatus.FAILED, started, error_detail)
            raise
        status = ToolCallStatus.OK if result else ToolCallStatus.EMPTY
        await self._record(ctx, operation, status, started, detail or {})
        logger.debug(
            "Operation completed status=%s elapsed_ms=%d result=%s",
            status.value, int((monotonic() - started) * 1000), self._result_summary(result),
            extra=log_context,
        )
        return result

    async def optional(self, ctx, operation, action):
        try:
            return await self.logged(ctx, operation, action)
        except (httpx.HTTPError, ValueError, KeyError) as error:
            logger.warning(
                "Optional operation degraded error_type=%s error_message=%s",
                type(error).__name__, sanitize_message(error), extra=self._context(ctx, operation),
            )
            notice = {
                OperationName.LOOKUP_MOVIE: "TMDB metadata could not be verified for some candidates.",
                OperationName.LOOKUP_RATINGS: "External ratings are unavailable for some films.",
            }.get(operation, "Some external sources are unavailable.")
            if notice not in ctx.limitations:
                ctx.limitations.append(notice)
            return None

    async def capture(self, ctx, rows):
        existing = {(item.film_id, item.source_url, item.body) for item in ctx.evidence}
        snapshots = []
        for row in rows:
            key = (row.film_id, row.source_url, row.body)
            if key not in existing:
                existing.add(key)
                snapshots.append(row)
        if snapshots:
            payload = [
                {**item.model_dump(mode="python"), "kind": item.kind.value}
                for item in snapshots
            ]
            persisted = await self.persistence.add_evidence(ctx.run_id, payload)
            ctx.evidence.extend(EvidenceRecord.model_validate(item) for item in persisted)
        logger.debug(
            "Evidence capture input_count=%d new_count=%d total_count=%d",
            len(rows), len(snapshots), len(ctx.evidence), extra=self._context(ctx, "evidence.capture"),
        )

    async def advance(self, ctx, stage, message):
        await self.persistence.advance_run(ctx.run_id, stage, message)

    async def _record(self, ctx, operation, status, started, detail):
        await self.persistence.record_tool_call(
            ctx.run_id, operation.value, status.value, int((monotonic() - started) * 1000), detail
        )

    @staticmethod
    def _context(ctx, operation):
        value = operation.value if isinstance(operation, OperationName) else str(operation)
        return {"run_id": str(ctx.run_id), "state": ctx.state.value, "operation": value}

    @staticmethod
    def _result_summary(result):
        if result is None:
            return "None"
        if isinstance(result, (list, tuple, set, dict)):
            return f"{type(result).__name__}[{len(result)}]"
        return type(result).__name__


class PlanNode:
    def __init__(self, operations: WorkflowOperations):
        self.operations = operations

    async def run(self, ctx: WorkflowContext) -> WorkflowContext:
        await self.operations.advance(ctx, ProgressStage.PLANNING, "Defining the search terms and scope.")
        ctx.plan = await self.operations.logged(ctx, OperationName.MODEL_PLAN, lambda: ctx.model.plan(ctx.constraints))
        ctx.outcome = NodeOutcome.SUCCESS
        return ctx


class DiscoverNode:
    def __init__(self, operations: WorkflowOperations):
        self.operations = operations

    async def run(self, ctx: WorkflowContext) -> WorkflowContext:
        await self.operations.advance(ctx, ProgressStage.SEARCHING, "Looking for films, metadata, and sources.")
        candidate_ids = await self.operations.logged(
            ctx, OperationName.FIND_CANDIDATES, lambda: ctx.tools.find_candidates(ctx.constraints.theme)
        )
        for film_id in candidate_ids:
            ctx.films.append(await self.operations.logged(
                ctx, OperationName.LOOKUP_LOCAL_MOVIE, lambda film_id=film_id: ctx.tools.lookup_local_movie(film_id)
            ))
        await self._metadata_candidates(ctx)
        for film in ctx.films:
            await self._film_evidence(ctx, film)
        context = await self.operations.logged(
            ctx, OperationName.SEARCH_PASSAGES_CONTEXT,
            lambda: ctx.tools.search_passages(ctx.require_plan().query)
        )
        if not context and ctx.tools.web_enabled:
            context = await self.operations.optional(
                ctx, OperationName.SEARCH_WEB_CONTEXT,
                lambda: ctx.tools.search_web(ctx.require_plan().query)
            )
        await self.operations.capture(ctx, context or [])
        ctx.outcome = NodeOutcome.SUCCESS
        return ctx

    async def _metadata_candidates(self, ctx):
        if not ctx.tools.metadata_enabled:
            return
        for hint in ctx.require_plan().hints:
            if len(ctx.films) >= 10:
                break
            if any(hint.title.casefold() in {film.original_title.casefold(), film.title.casefold()}
                   and (hint.year is None or film.year == hint.year) for film in ctx.films):
                continue
            film = await self.operations.optional(ctx, OperationName.LOOKUP_MOVIE, lambda hint=hint: ctx.tools.lookup_movie(hint))
            if film and all(item.id != film.id for item in ctx.films):
                ctx.films.append(film)

    async def _film_evidence(self, ctx, film):
        film_id = film.id
        await self.operations.capture(ctx, [EvidenceDraft(
            passage_id=None, film_id=film_id, kind=EvidenceKind.METADATA, locator="Film catalog",
            source_title=film.title, source_url=film.releases[0].source_url,
            body=json.dumps({"year": film.year, "countries": film.countries,
                             "director": film.director,
                             "releases": [item.model_dump(mode="json") for item in film.releases]}, ensure_ascii=False),
        )])
        rows = await self.operations.logged(
            ctx, OperationName.SEARCH_PASSAGES,
            lambda: ctx.tools.search_passages(ctx.require_plan().query, film_id)
        )
        if not rows and ctx.tools.web_enabled:
            query = f"\"{film.original_title}\" {film.year} film synopsis historical setting"
            rows = await self.operations.optional(ctx, OperationName.SEARCH_WEB, lambda: ctx.tools.search_web(query, film_id))
        await self.operations.capture(ctx, rows or [])


class AssessNode:
    def __init__(self, operations: WorkflowOperations):
        self.operations = operations

    async def run(self, ctx: WorkflowContext) -> WorkflowContext:
        if ctx.assessment_films is None:
            await self.operations.advance(
                ctx, ProgressStage.ASSESSING, f"Checking criteria and evidence for {len(ctx.films)} candidate films."
            )
            films = ctx.films
        else:
            films = ctx.assessment_films
        ctx.assessment_films = []
        if films:
            ctx.decisions.update(await self._assess(ctx, films))
        accepted = sum(item.status == DecisionStatus.ACCEPTED for item in ctx.decisions.values())
        ctx.pending_follow_up = None
        if accepted < ctx.constraints.count and len(ctx.seen_follow_up_queries) < MAX_FOLLOW_UP_QUERIES:
            ctx.pending_follow_up = next((
                FollowUpRequest(film, item.next_query) for film in ctx.films
                if (item := ctx.decisions[film.id]).status == DecisionStatus.NEEDS_EVIDENCE
                and item.next_query
                and ctx.follow_up_attempts[film.id] < MAX_FOLLOW_UPS_PER_FILM
                and FollowUpRequest(film, item.next_query).key not in ctx.seen_follow_up_queries
            ), None)
        ctx.outcome = NodeOutcome.FOLLOW_UP if ctx.pending_follow_up else NodeOutcome.SUCCESS
        return ctx

    async def _assess(self, ctx, films):
        raw = await self.operations.logged(
            ctx, OperationName.MODEL_ASSESS, lambda: ctx.model.assess(ctx.constraints, films, ctx.evidence)
        )
        by_id = {item.id: item for item in ctx.evidence}
        decisions = defaultdict(list)
        for decision in raw.decisions:
            decisions[decision.film_id].append(decision)
        result = {}
        for film in films:
            film_id = film.id
            items = decisions[film_id]
            if len(items) != 1:
                result[film_id] = ValidatedDecision(
                    film_id=film_id, status=DecisionStatus.NEEDS_EVIDENCE,
                    reason="The model did not return a unique decision for this film.",
                )
                continue
            assessment: FilmAssessment = items[0]
            evidence_ids = [ref for ref in assessment.evidence_ids if ref in by_id
                            and by_id[ref].film_id in {None, film_id}]
            item = ValidatedDecision.model_validate(
                {**assessment.model_dump(), "evidence_ids": evidence_ids}
            )
            if item.status == DecisionStatus.ACCEPTED and not any(
                by_id[ref].film_id == film_id and by_id[ref].kind != EvidenceKind.METADATA
                for ref in item.evidence_ids
            ):
                item = item.model_copy(update={
                    "status": DecisionStatus.NEEDS_EVIDENCE,
                    "reason": "The selection lacks a source that connects this film to the topic.",
                })
            result[film_id] = item
        return result


class FollowUpNode:
    def __init__(self, operations: WorkflowOperations):
        self.operations = operations

    async def run(self, ctx: WorkflowContext) -> WorkflowContext:
        if ctx.pending_follow_up is None:
            raise RuntimeError("follow-up state has no pending target")
        request = ctx.pending_follow_up
        film, query = request.film, request.query
        film_id = film.id
        ctx.seen_follow_up_queries.add(request.key)
        ctx.follow_up_attempts[film.id] += 1
        await self.operations.advance(ctx, ProgressStage.FOLLOW_UP, f"Checking additional sources for {film.title}.")
        before = len(ctx.evidence)
        search = ctx.tools.search_web if ctx.tools.web_enabled else ctx.tools.search_passages
        rows = await self.operations.optional(
            ctx, OperationName.SEARCH_WEB_FOLLOW_UP if ctx.tools.web_enabled else OperationName.SEARCH_PASSAGES_FOLLOW_UP,
            lambda: search(query, film_id),
        )
        await self.operations.capture(ctx, rows or [])
        ctx.assessment_films = [film] if len(ctx.evidence) > before else []
        ctx.pending_follow_up = None
        ctx.outcome = NodeOutcome.SUCCESS
        return ctx


class SelectNode:
    def __init__(self, operations: WorkflowOperations):
        self.operations = operations

    async def run(self, ctx: WorkflowContext) -> WorkflowContext:
        matches = [film for film in ctx.films if ctx.decisions[film.id].status == DecisionStatus.ACCEPTED]
        matches.sort(key=lambda film: ctx.decisions[film.id].relevance == Relevance.SECONDARY)
        ctx.selected = matches[:ctx.constraints.count]
        selected_ids = {film.id for film in ctx.selected}
        ctx.published_evidence = [
            item for item in ctx.evidence if item.film_id is None or item.film_id in selected_ids
        ]
        if len(ctx.selected) < ctx.constraints.count:
            ctx.limitations.append(
                f"Requested {ctx.constraints.count} films. Found sufficient evidence for {len(ctx.selected)}."
            )
        if not ctx.films:
            ctx.limitations.append("No candidates were found. Try an example question or enable external film tools.")
        ctx.study = StudyPayload(
            films=ctx.selected,
            decisions=[StudyDecision(
                title=film.title, year=film.year, **ctx.decisions[film.id].model_dump()
            ) for film in ctx.films],
            evidence=ctx.evidence, limitations=ctx.limitations,
            follow_ups=len(ctx.seen_follow_up_queries),
        )
        await self.operations.persistence.save_study(
            ctx.run_id,
            [film.model_dump(mode="json") for film in ctx.films],
            {str(key): item.model_dump(mode="json") for key, item in ctx.decisions.items()},
            ctx.study.to_json(),
        )
        if not ctx.selected:
            ctx.partial_message = "The available sources did not support any films meeting the criteria."
            ctx.outcome = NodeOutcome.PARTIAL
        else:
            ctx.outcome = NodeOutcome.SUCCESS
        return ctx


class TimelineNode:
    def __init__(self, operations: WorkflowOperations, builder: TimelineBuilder):
        self.operations = operations
        self.builder = builder

    async def run(self, ctx: WorkflowContext) -> WorkflowContext:
        await self.operations.advance(ctx, ProgressStage.GENERATING, "Writing the viewing guide and building the timeline.")
        research = TimelineResearch()
        try:
            research = await self.operations.logged(
                ctx, OperationName.MODEL_TIMELINE,
                lambda: ctx.model.timeline(ctx.constraints, ctx.selected, ctx.published_evidence)
            )
        except (APIError, ValueError):
            ctx.limitations.append("The story or context timeline could not be validated.")
        timeline = self.builder.build(ctx.selected, research, {item.id for item in ctx.published_evidence})
        study = ctx.require_study()
        ctx.study = study.model_copy(update={"timeline": timeline})
        views = timeline.views_json()
        await self.operations.persistence.save_timeline(
            ctx.run_id, ctx.study.to_json(), views, timeline.valid_edges,
            [item.model_dump(mode="json") for item in ctx.published_evidence],
        )
        ctx.outcome = NodeOutcome.SUCCESS
        return ctx


class GuideNode:
    def __init__(self, operations: WorkflowOperations, validator: GuideValidator):
        self.operations = operations
        self.validator = validator

    async def run(self, ctx: WorkflowContext) -> WorkflowContext:
        operation = OperationName.MODEL_GENERATE if ctx.guide_attempts == 0 else OperationName.MODEL_REVISE
        ctx.guide_attempts += 1
        try:
            guide = await self.operations.logged(
                ctx, operation,
                lambda: ctx.model.generate(
                    ctx.constraints, ctx.selected, ctx.published_evidence, ctx.guide_correction
                ),
            )
            self.validator.validate(guide, ctx.selected, ctx.published_evidence)
            ctx.guide = guide
            ctx.outcome = NodeOutcome.SUCCESS
        except (ValidationError, ValueError) as error:
            ctx.guide_correction = str(error)[:600]
            ctx.outcome = NodeOutcome.RETRY if ctx.guide_attempts < MAX_GUIDE_ATTEMPTS else NodeOutcome.PARTIAL
        except APIError as error:
            detail = "The model service returned an error while writing the guide."
            if isinstance(error, APITimeoutError):
                detail = "The model timed out while writing the guide."
            elif isinstance(error, RateLimitError):
                detail = "The model service was rate limited while writing the guide."
            await self.operations.advance(ctx, ProgressStage.GENERATING, detail)
            ctx.outcome = NodeOutcome.PARTIAL
        return ctx


class RatingsNode:
    def __init__(self, operations: WorkflowOperations):
        self.operations = operations

    async def run(self, ctx: WorkflowContext) -> WorkflowContext:
        if ctx.tools.ratings_enabled:
            await asyncio.gather(*(self.operations.optional(
                ctx, OperationName.LOOKUP_RATINGS, lambda film=film: ctx.tools.lookup_ratings(film)
            ) for film in ctx.selected))
        ctx.outcome = NodeOutcome.SUCCESS if ctx.guide is not None else NodeOutcome.PARTIAL
        return ctx


class PublishNode:
    def __init__(self, operations: WorkflowOperations):
        self.operations = operations

    async def run(self, ctx: WorkflowContext) -> WorkflowContext:
        await self.operations.advance(ctx, ProgressStage.VALIDATING, "Checking film coverage and citations.")
        status = RunStatus.PARTIAL if len(ctx.selected) < ctx.constraints.count else RunStatus.COMPLETED
        await self.operations.persistence.finish_run(
            ctx.run_id, status, "The viewing guide is ready.", ctx.require_guide().model_dump(mode="json")
        )
        ctx.outcome = NodeOutcome.SUCCESS
        return ctx


class PublishPartialNode:
    def __init__(self, operations: WorkflowOperations):
        self.operations = operations

    async def run(self, ctx: WorkflowContext) -> WorkflowContext:
        if ctx.selected:
            await self.operations.advance(ctx, ProgressStage.VALIDATING, "Checking film coverage and citations.")
        await self.operations.persistence.finish_run(ctx.run_id, RunStatus.PARTIAL, ctx.partial_message)
        ctx.outcome = NodeOutcome.SUCCESS
        return ctx


def create_workflow(persistence: CurationPersistence):
    operations = WorkflowOperations(persistence)
    timeline_builder = TimelineBuilder()
    guide_validator = GuideValidator()
    nodes = {
        CurationState.PLAN: PlanNode(operations),
        CurationState.DISCOVER: DiscoverNode(operations),
        CurationState.ASSESS: AssessNode(operations),
        CurationState.FOLLOW_UP: FollowUpNode(operations),
        CurationState.SELECT: SelectNode(operations),
        CurationState.TIMELINE: TimelineNode(operations, timeline_builder),
        CurationState.GUIDE: GuideNode(operations, guide_validator),
        CurationState.RATINGS: RatingsNode(operations),
        CurationState.PUBLISH: PublishNode(operations),
        CurationState.PUBLISH_PARTIAL: PublishPartialNode(operations),
    }
    transitions = TransitionEngine([
        WorkflowTransition(CurationState.PLAN, NodeOutcome.SUCCESS, CurationState.DISCOVER),
        WorkflowTransition(CurationState.DISCOVER, NodeOutcome.SUCCESS, CurationState.ASSESS),
        WorkflowTransition(CurationState.ASSESS, NodeOutcome.FOLLOW_UP, CurationState.FOLLOW_UP),
        WorkflowTransition(CurationState.ASSESS, NodeOutcome.SUCCESS, CurationState.SELECT),
        WorkflowTransition(CurationState.FOLLOW_UP, NodeOutcome.SUCCESS, CurationState.ASSESS),
        WorkflowTransition(CurationState.SELECT, NodeOutcome.SUCCESS, CurationState.TIMELINE),
        WorkflowTransition(CurationState.SELECT, NodeOutcome.PARTIAL, CurationState.PUBLISH_PARTIAL),
        WorkflowTransition(CurationState.TIMELINE, NodeOutcome.SUCCESS, CurationState.GUIDE),
        WorkflowTransition(CurationState.GUIDE, NodeOutcome.RETRY, CurationState.GUIDE),
        WorkflowTransition(CurationState.GUIDE, NodeOutcome.SUCCESS, CurationState.RATINGS),
        WorkflowTransition(CurationState.GUIDE, NodeOutcome.PARTIAL, CurationState.RATINGS),
        WorkflowTransition(CurationState.RATINGS, NodeOutcome.SUCCESS, CurationState.PUBLISH),
        WorkflowTransition(CurationState.RATINGS, NodeOutcome.PARTIAL, CurationState.PUBLISH_PARTIAL),
        WorkflowTransition(CurationState.PUBLISH, NodeOutcome.SUCCESS, CurationState.COMPLETED),
        WorkflowTransition(CurationState.PUBLISH_PARTIAL, NodeOutcome.SUCCESS, CurationState.COMPLETED),
    ])
    return nodes, transitions


def create_guide_retry_workflow(persistence: CurationPersistence):
    operations = WorkflowOperations(persistence)
    nodes = {
        CurationState.GUIDE: GuideNode(operations, GuideValidator()),
        CurationState.PUBLISH: PublishNode(operations),
        CurationState.PUBLISH_PARTIAL: PublishPartialNode(operations),
    }
    transitions = TransitionEngine([
        WorkflowTransition(CurationState.GUIDE, NodeOutcome.RETRY, CurationState.GUIDE),
        WorkflowTransition(CurationState.GUIDE, NodeOutcome.SUCCESS, CurationState.PUBLISH),
        WorkflowTransition(CurationState.GUIDE, NodeOutcome.PARTIAL, CurationState.PUBLISH_PARTIAL),
        WorkflowTransition(CurationState.PUBLISH, NodeOutcome.SUCCESS, CurationState.COMPLETED),
        WorkflowTransition(CurationState.PUBLISH_PARTIAL, NodeOutcome.SUCCESS, CurationState.COMPLETED),
    ])
    return nodes, transitions
