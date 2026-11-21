import asyncio
import json
import logging
import re
from collections import defaultdict
from itertools import zip_longest
from time import monotonic
from uuid import UUID

import httpx
from openai import APIError, APITimeoutError, RateLimitError
from pydantic import ValidationError

from ..application.ports import CurationPersistence
from ..contracts import FilmAssessment, TimelineResearch
from ..domain import RunStatus
from ..observability import sanitize_message
from .activity import joined_titles, shorten
from .guide_validation import GuideValidator
from .ports import ModelUnavailableError
from .timeline import TimelineBuilder, calendar_years
from .types import (
    DecisionStatus,
    EvidenceDraft,
    EvidenceKind,
    EvidenceRecord,
    FollowUpRequest,
    ModelMode,
    OperationName,
    ProgressStage,
    Relevance,
    StudyDecision,
    StudyPayload,
    ToolCallStatus,
    ValidatedDecision,
    film_title_key,
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
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError) as error:
            logger.warning(
                "Optional operation degraded error_type=%s error_message=%s",
                type(error).__name__, sanitize_message(error), extra=self._context(ctx, operation),
            )
            notice = {
                OperationName.DISCOVER_CATALOG: "TMDB keyword discovery was unavailable.",
                OperationName.LOOKUP_MOVIE: "TMDB metadata could not be verified for some candidates.",
                OperationName.LOOKUP_RATINGS: "External ratings are unavailable for some films.",
                OperationName.SEARCH_WEB: "Some film source lookups could not be completed.",
                OperationName.SEARCH_EDITORIAL: "Some film editorial lookups could not be completed.",
                OperationName.SEARCH_WEB_CONTEXT: "Some historical context lookups could not be completed.",
                OperationName.SEARCH_WEB_FOLLOW_UP: "Some follow-up source lookups could not be completed.",
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
        await self.operations.advance(ctx, ProgressStage.PLANNING, "Prepared the search plan and backup film leads.")
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
        await self.operations.advance(
            ctx, ProgressStage.SEARCHING,
            f"Found {len(candidate_ids)} reusable film candidate{'s' if len(candidate_ids) != 1 else ''} in the local catalog."
        )
        if ctx.tools.metadata_enabled:
            local_overflow = ctx.films[6:]
            del ctx.films[6:]
            await self._metadata_candidates(ctx, local_overflow)
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

    async def _metadata_candidates(self, ctx, local_overflow):
        plan = ctx.require_plan()
        editorial = await self.operations.logged(
            ctx, OperationName.DISCOVER_EDITORIAL,
            lambda: ctx.tools.discover_editorial(ctx.constraints, plan.discovery_terms),
        )
        if editorial:
            await self.operations.advance(ctx, ProgressStage.SEARCHING,
                                          f"Found {len(editorial)} film leads in editorial sources.")
        async def search_catalog():
            return await asyncio.wait_for(ctx.tools.discover_catalog(plan.discovery_terms, ctx.constraints), timeout=18)

        catalog = await self.operations.optional(ctx, OperationName.DISCOVER_CATALOG, search_catalog) or []
        if catalog:
            await self.operations.advance(ctx, ProgressStage.SEARCHING,
                                          f"Found {len(catalog)} film leads through TMDB keywords.")
        leads = (hint for batch in zip_longest(editorial, catalog, plan.hints)
                 for hint in batch if hint is not None)
        checked = set()
        for hint in leads:
            if len(ctx.films) >= 10:
                break
            title_key = film_title_key(hint.title)
            key = (title_key, hint.year)
            if key in checked:
                continue
            checked.add(key)
            if any(title_key in {film_title_key(film.original_title), film_title_key(film.title)}
                   and (hint.year is None or film.year == hint.year) for film in ctx.films):
                continue
            await self.operations.advance(
                ctx, ProgressStage.SEARCHING,
                f"Checking film metadata for “{hint.title}”{f' ({hint.year})' if hint.year else ''}."
            )
            film = await self.operations.optional(ctx, OperationName.LOOKUP_MOVIE, lambda hint=hint: ctx.tools.lookup_movie(hint))
            if film and not any(self._same_film(film, item) for item in ctx.films):
                ctx.films.append(film)
        for film in local_overflow:
            if len(ctx.films) >= 10:
                break
            if not any(self._same_film(film, item) for item in ctx.films):
                ctx.films.append(film)

    @staticmethod
    def _same_film(left, right):
        for key in ("id", "tmdb_id", "imdb_id"):
            value = getattr(left, key)
            if value and value == getattr(right, key):
                return True
        if left.year != right.year:
            return False
        if left.director and right.director and film_title_key(left.director) != film_title_key(right.director):
            return False
        left_titles = {film_title_key(left.title), film_title_key(left.original_title)} - {""}
        right_titles = {film_title_key(right.title), film_title_key(right.original_title)}
        return bool(left_titles & right_titles)

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
        external = await self.operations.optional(
            ctx, OperationName.SEARCH_EDITORIAL,
            lambda: ctx.tools.search_editorial(film, live=ctx.model.mode != ModelMode.MOCK),
        )
        if not rows and ctx.tools.web_enabled:
            await self.operations.advance(
                ctx, ProgressStage.SEARCHING,
                f"Checking public background sources for “{film.title}”."
            )
            query = f"\"{film.original_title}\" {film.year} film synopsis historical setting"
            rows = await self.operations.optional(ctx, OperationName.SEARCH_WEB, lambda: ctx.tools.search_web(query, film_id))
        await self.operations.capture(ctx, [*(rows or []), *(external or [])])


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
            for film in films:
                decision = ctx.decisions[film.id]
                await self.operations.advance(
                    ctx, ProgressStage.ASSESSING,
                    f"{film.title}: {decision.status.value.replace('_', ' ')} — {shorten(decision.reason)}"
                )
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
        film_ids = {film.id for film in films}
        evidence = [source for source in ctx.evidence if source.film_id is None or source.film_id in film_ids]
        raw = await self.operations.logged(
            ctx, OperationName.MODEL_ASSESS, lambda: ctx.model.assess(ctx.constraints, films, evidence)
        )
        by_id = {item.id: item for item in ctx.evidence}
        decisions = defaultdict(list)
        for decision in raw.decisions:
            decisions[decision.film_id].append(decision)
        result = {}
        setting = ctx.constraints.story_period
        years = calendar_years(setting)
        exact_year = next(iter(years), None) if (
            len(years) == 1
            and not re.search(
                r"\b(?:circa|around|about|before|after|century)\b|\bc\.\s*\d|\d{4}s\b|\d\s*[-–—/]\s*\d",
                setting, re.I,
            )
        ) else None
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
            has_topic_evidence = any(
                by_id[ref].kind != EvidenceKind.METADATA for ref in item.evidence_ids
            )
            if item.status != DecisionStatus.REJECTED and not has_topic_evidence:
                item = item.model_copy(update={
                    "status": DecisionStatus.NEEDS_EVIDENCE,
                    "next_query": f'"{film.original_title}" {film.year} film {ctx.constraints.theme}',
                })
            if item.status == DecisionStatus.ACCEPTED and exact_year is not None:
                quote = " ".join(assessment.setting_quote.casefold().split())
                if exact_year not in calendar_years(quote) or not any(
                    by_id[ref].film_id == film_id and by_id[ref].kind != EvidenceKind.METADATA
                    and quote in " ".join(by_id[ref].body.casefold().split())
                    for ref in evidence_ids
                ):
                    if ctx.constraints.setting_match == "strict":
                        item = item.model_copy(update={
                            "status": DecisionStatus.NEEDS_EVIDENCE,
                            "reason": f"The cited sources do not establish a story setting in {exact_year}.",
                            "next_query": assessment.next_query or f'"{film.title}" {film.year} film story set in {exact_year}',
                        })
                    else:
                        item = item.model_copy(update={"relevance": Relevance.SECONDARY})
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
        if ctx.model.mode != ModelMode.MOCK and not any(
            item.film_id == film_id and item.kind == EvidenceKind.EDITORIAL for item in ctx.evidence
        ):
            reviews = await self.operations.optional(
                ctx, OperationName.SEARCH_EDITORIAL, lambda: ctx.tools.search_editorial(film, focus=query),
            )
            await self.operations.capture(ctx, reviews or [])
        if ctx.tools.web_enabled:
            context_rows = await self.operations.optional(
                ctx, OperationName.SEARCH_WEB_CONTEXT,
                lambda: ctx.tools.search_web(query),
            )
            await self.operations.capture(ctx, context_rows or [])
        new_count = len(ctx.evidence) - before
        await self.operations.advance(
            ctx, ProgressStage.FOLLOW_UP,
            (f"Found {new_count} additional source{'s' if new_count != 1 else ''} for “{film.title}”; re-evaluating the film."
             if new_count else f"No additional evidence was found for “{film.title}”.")
        )
        ctx.assessment_films = [film] if new_count else []
        ctx.pending_follow_up = None
        ctx.outcome = NodeOutcome.SUCCESS
        return ctx


class SelectNode:
    def __init__(self, operations: WorkflowOperations):
        self.operations = operations

    async def run(self, ctx: WorkflowContext) -> WorkflowContext:
        matches = [film for film in ctx.films if ctx.decisions[film.id].status == DecisionStatus.ACCEPTED]
        matches.sort(key=lambda film: (
            ctx.decisions[film.id].relevance == Relevance.SECONDARY,
            -ctx.decisions[film.id].preference_fit,
        ))
        ctx.selected = matches[:ctx.constraints.count]
        selected_ids = {film.id for film in ctx.selected}
        ctx.published_evidence = [
            item for item in ctx.evidence if item.film_id is None or item.film_id in selected_ids
        ]
        if len(ctx.selected) < ctx.constraints.count:
            ctx.limitations.append(
                f"Requested {ctx.constraints.count} films. Verified {len(ctx.selected)} against the confirmed criteria."
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
        if ctx.selected:
            await self.operations.advance(
                ctx, ProgressStage.ASSESSING,
                f"Selected {len(ctx.selected)} film{'s' if len(ctx.selected) != 1 else ''}: {joined_titles(ctx.selected)}."
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
        except (APIError, ModelUnavailableError, ValueError):
            ctx.limitations.append("The story or context timeline could not be validated.")
        timeline = self.builder.build(ctx.selected, research, ctx.published_evidence)
        await self.operations.advance(
            ctx, ProgressStage.GENERATING,
            f"Built validated timeline views for {', '.join(axis.value for axis in timeline.views)}."
        )
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
        await self.operations.advance(
            ctx, ProgressStage.GENERATING,
            f"Writing guide draft {ctx.guide_attempts} of {MAX_GUIDE_ATTEMPTS} for {len(ctx.selected)} selected films."
        )

        async def generate_validated_guide():
            guide = await ctx.model.generate(
                ctx.constraints, ctx.selected, ctx.published_evidence, ctx.guide_correction
            )
            self.validator.validate(guide, ctx.selected, ctx.published_evidence)
            return guide

        try:
            guide = await self.operations.logged(ctx, operation, generate_validated_guide)
            await self.operations.advance(
                ctx, ProgressStage.GENERATING,
                "The guide passed film coverage and citation validation."
            )
            ctx.guide = guide
            ctx.outcome = NodeOutcome.SUCCESS
        except (ValidationError, ValueError) as error:
            ctx.guide_correction = sanitize_message(error, 600)
            logger.warning("Guide draft rejected: %s", ctx.guide_correction,
                           extra=self.operations._context(ctx, operation))
            can_retry = ctx.guide_attempts < MAX_GUIDE_ATTEMPTS
            await self.operations.advance(
                ctx, ProgressStage.GENERATING,
                "Revising the guide to fix a format or citation error." if can_retry
                else "The revised guide could not be verified. The selected films and sources were saved."
            )
            ctx.outcome = NodeOutcome.RETRY if can_retry else NodeOutcome.PARTIAL
        except (APIError, ModelUnavailableError) as error:
            detail = "The model service returned an error while writing the guide."
            if isinstance(error, ModelUnavailableError):
                detail = str(error)
            elif isinstance(error, APITimeoutError):
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
        if ctx.tools.metadata_enabled:
            await asyncio.gather(*(self.operations.optional(
                ctx, OperationName.LOOKUP_MOVIE, lambda film=film: ctx.tools.enrich_local_movie(film)
            ) for film in ctx.selected if not film.tmdb_id and not film.poster_path))
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
