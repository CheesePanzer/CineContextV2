import unittest
import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
from alcedo import ConflictError, NotFoundError, Options, ServiceUnavailableError
from pydantic import BaseModel
from starlette.testclient import TestClient
from cinecontext.ai.curation import CurationService
from cinecontext.ai.openai_model import ModelOutputValidationError, ModelResponse, OpenAIModel
from cinecontext.ai.tacc_transport import TaccSdkTransport
from cinecontext.ai.nodes import AssessNode, DiscoverNode, WorkflowOperations
from cinecontext.application.services import FilmReadingsApplicationService, ParseApplicationService, RunApplicationService
from cinecontext.ai.types import (
    DecisionStatus,
    EvidenceKind,
    EvidenceRecord,
    FilmProjection,
    FilmReleaseProjection,
    ResearchTimelineAxis,
    StudyDecision,
    StudyPayload,
    ValidatedTimeline,
    TimelineAxis,
    OperationName,
    ModelReadiness,
)
from cinecontext.ai.research_tools import create_research_tools
from cinecontext.ai.research_tools.crawler import VisibleHTMLParser, validate_public_url
from cinecontext.ai.research_tools.duckduckgo import DuckDuckGoHTMLParser
from cinecontext.ai.research_tools.editorial import WebFilmReadings, article_url, curated_candidates
from cinecontext.ai.research_tools.tmdb import TmdbMetadataProvider
from cinecontext.ai.guide_validation import GuideValidator
from cinecontext.ai.guide_draft import GuideDraft
from cinecontext.ai.timeline import TimelineBuilder
from cinecontext.ai.workflow import (
    CurationState,
    NodeOutcome,
    TransitionEngine,
    WorkflowContext,
    WorkflowRunner,
    WorkflowRunnerError,
    WorkflowTransition,
    WorkflowTransitionError,
)
from cinecontext.ai.resume import GuideRetrySnapshot
from cinecontext.contracts import (
    Assessment, Claim, Constraints, FilmGuide, FilmHint, Guide, ParsedRequest, ParseRequest,
    Plan, ReferenceMatch, TimelineResearch,
)
from cinecontext.domain import CurationRun, RunStatus
from cinecontext.observability import configure_logging, sanitize_message
from cinecontext.settings import LoggingOptions, ModelOptions, TmdbOptions, ToolsOptions
from cinecontext.infrastructure.tacc_jobs import TaccJobs
from cinecontext.main import create_app
from cinecontext.spa import SpaStaticFiles


def context(state=CurationState.PLAN):
    return WorkflowContext(
        uuid4(), Constraints(topic="test", theme="test", count=1),
        SimpleNamespace(), SimpleNamespace(), state=state
    )


async def async_value(value):
    return value


class OutcomeNode:
    def __init__(self, outcome):
        self.outcome = outcome

    async def run(self, ctx: WorkflowContext) -> WorkflowContext:
        ctx.outcome = self.outcome
        return ctx


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def test_enum_json_values_are_unchanged(self):
        constraints = Constraints(topic="test", theme="test", axis=TimelineAxis.HISTORY)
        self.assertEqual(constraints.model_dump(mode="json")["axis"], "history")
        self.assertEqual(DecisionStatus.NEEDS_EVIDENCE.value, "needs_evidence")
        self.assertEqual(EvidenceKind.WEB_EXCERPT.value, "web_excerpt")

    async def test_source_backed_setting_fills_empty_criteria_and_keeps_explicit_setting(self):
        prompt = "Find films set around the time of a named work."
        match = ReferenceMatch(
            title="Named work", story_setting="Bohemia in 1403",
            source_title="Publisher", source_url="https://example.com/work",
        )
        for supplied, mode, found, expected in (
            ("", "nearby", match, "Bohemia in the early 15th century"),
            ("", "nearby", match.model_copy(update={"story_setting": "1403, Kingdom of Bohemia"}),
             "Early 15th century, Kingdom of Bohemia"),
            ("", "nearby", match.model_copy(update={"story_setting": "Bohemia, 1400–1420"}), "Bohemia, 1400–1420"),
            ("", "strict", match, "Bohemia in 1403"),
            ("Prague in the 1420s", "nearby", match, "Prague in the 1420s"),
            ("the time period of the named work", "nearby", None, ""),
        ):
            with self.subTest(supplied=supplied, mode=mode, found=found):
                parsed = ParsedRequest(
                    constraints=Constraints(topic=prompt, theme="Historical films", story_period=supplied,
                                            setting_match=mode),
                    reference_queries=["named work"], clarification="Confirm the title.",
                )
                model = SimpleNamespace(
                    parse=AsyncMock(return_value=parsed),
                    resolve_reference=AsyncMock(return_value=found),
                    close=AsyncMock(),
                )
                references = SimpleNamespace(search=AsyncMock(return_value=[sample_evidence(None)]))
                result = await ParseApplicationService(SimpleNamespace(create=lambda: model), references).parse(
                    ParseRequest(prompt=prompt),
                )
                self.assertEqual(result.constraints.story_period, expected)
                self.assertEqual(result.constraints.setting_match, mode)
                self.assertEqual(result.reference_match, found)
                self.assertEqual(bool(result.clarification), found is None)

    async def test_parsed_title_cannot_block_the_verified_story_setting(self):
        title = "Kingdom Come: Deliverance II"
        prompt = f"I'm interested in the time period of 《{title}》. Find similar films."
        match = ReferenceMatch(
            title=title, story_setting="1403, Kingdom of Bohemia",
            source_title="Publisher", source_url="https://example.com/work",
        )
        for model_queries, found, expected in (
            ([f"{title} setting", "Kingdom Come Deliverance 2 historical period"], match,
             "Early 15th century, Kingdom of Bohemia"),
            ([], match, "Early 15th century, Kingdom of Bohemia"),
            ([], None, ""),
        ):
            with self.subTest(model_queries=model_queries, found=bool(found)):
                parsed = ParsedRequest(
                    constraints=Constraints(topic=prompt, theme="Historical films",
                                            story_period="《KINGDOM COME — DELIVERANCE II》", setting_match="nearby"),
                    reference_queries=model_queries,
                )
                model = SimpleNamespace(
                    parse=AsyncMock(return_value=parsed),
                    resolve_reference=AsyncMock(return_value=found),
                    close=AsyncMock(),
                )
                references = SimpleNamespace(search=AsyncMock(return_value=[sample_evidence(None)]))
                result = await ParseApplicationService(SimpleNamespace(create=lambda: model), references).parse(
                    ParseRequest(prompt=prompt),
                )
                self.assertEqual(result.constraints.story_period, expected)
                self.assertEqual(result.constraints.setting_match, "nearby")
                self.assertEqual(result.reference_match, found)
                self.assertEqual(bool(result.clarification), found is None)
                references.search.assert_awaited_once_with(model_queries or [f"{title} setting"])

    def test_study_payload_has_exact_external_keys(self):
        film = sample_film()
        evidence = sample_evidence(film.id)
        payload = StudyPayload(
            films=[film],
            decisions=[StudyDecision(
                film_id=film.id, title=film.title, year=film.year,
                status=DecisionStatus.ACCEPTED, reason="Supported", evidence_ids=[evidence.id],
            )],
            evidence=[evidence], limitations=[], follow_ups=0,
        ).to_json()
        self.assertEqual(
            set(payload), {"films", "decisions", "evidence", "limitations", "follow_ups"}
        )

        with_timeline = StudyPayload(
            films=[film],
            decisions=[],
            evidence=[evidence],
            limitations=[],
            follow_ups=0,
            timeline=ValidatedTimeline(),
        ).to_json()
        self.assertEqual(with_timeline["timeline_views"], {})

    def test_timeline_shape_preserves_release_and_model_event_fields(self):
        film = sample_film()
        evidence = sample_evidence(film.id).model_copy(update={"body": "The film is set in 1990."})
        research = TimelineResearch.model_validate({
            "events": [{
                "id": "story-event", "axis": ResearchTimelineAxis.STORY,
                "label": "Setting", "period": "1990", "sort_year": 1990,
                "film_ids": [film.id], "relation": "Setting", "evidence_ids": [evidence.id],
            }],
            "order": [],
        })
        views = TimelineBuilder().build([film], research, [evidence]).views_json()
        self.assertEqual(set(views), {"release", "story"})
        self.assertEqual(
            set(views["release"][0]),
            {"id", "label", "period", "sort_year", "film_ids", "evidence_ids", "relation"},
        )
        self.assertEqual(
            set(views["story"][0]),
            {"id", "axis", "label", "period", "sort_year", "version", "film_ids",
             "relation", "evidence_ids", "order_group"},
        )

    def test_story_year_requires_an_explicit_year_in_film_source(self):
        film = sample_film()
        evidence = sample_evidence(film.id).model_copy(update={
            "body": "The story takes place sixteen years after the 1989 revolution.",
        })
        research = TimelineResearch.model_validate({"events": [{
            "id": "incorrect-year", "axis": "story", "label": "The town remembers the revolution",
            "period": "2004", "sort_year": 2004, "film_ids": [film.id], "evidence_ids": [evidence.id],
        }]})
        timeline = TimelineBuilder().build([film], research, [evidence])
        self.assertNotIn(TimelineAxis.STORY, timeline.views)

        research.events[0].period = "1,000 AD"
        research.events[0].sort_year = 1000
        timeline = TimelineBuilder().build([film], research, [evidence])
        self.assertNotIn(TimelineAxis.STORY, timeline.views)

    def test_history_orders_documented_dates_and_ignores_reversed_model_edges(self):
        evidence = sample_evidence(None)
        dates = [
            ("campaign", "August 1942–February 1943", 1942),
            ("year-only", "1942–1943", 1942),
            ("midway", "1942-06-04", 1942),
            ("pearl-harbor", "December 7, 1941", 1941),
            ("coral-sea", "4–8 May 1942", 1942),
            ("earlier-in-may", "May 1, 1942", 1942),
        ]
        research = TimelineResearch.model_validate({
            "events": [
                {"id": name, "axis": "history", "label": name, "period": period,
                 "sort_year": year, "evidence_ids": [evidence.id]}
                for name, period, year in dates
            ],
            "order": [
                {"earlier": "campaign", "later": "coral-sea", "evidence_ids": [evidence.id]},
                {"earlier": "coral-sea", "later": "midway", "evidence_ids": [evidence.id]},
            ],
        })
        result = TimelineBuilder().build([], research, [evidence])
        self.assertEqual([event["id"] for event in result.views_json()["history"]], [
            "pearl-harbor", "earlier-in-may", "coral-sea", "midway", "campaign", "year-only",
        ])
        self.assertEqual([(edge.earlier, edge.later) for edge in result.valid_edges], [
            ("coral-sea", "midway"),
        ])

    def test_story_event_cannot_borrow_another_films_source(self):
        film = sample_film()
        other = film.model_copy(update={"id": uuid4()})
        evidence = sample_evidence(other.id)
        research = TimelineResearch.model_validate({"events": [{
            "id": "wrong-film", "axis": "story", "label": "Setting", "period": "1990",
            "film_ids": [film.id], "evidence_ids": [evidence.id],
        }]})
        timeline = TimelineBuilder().build([film, other], research, [evidence])
        self.assertNotIn(TimelineAxis.STORY, timeline.views)

    def test_timeline_filters_events_with_unpublished_evidence(self):
        film = sample_film()
        research = TimelineResearch.model_validate({
            "events": [{
                "id": "unsupported", "axis": ResearchTimelineAxis.STORY,
                "label": "Setting", "period": "1990", "sort_year": 1990,
                "film_ids": [film.id], "relation": "Setting", "evidence_ids": [uuid4()],
            }],
        })

        timeline = TimelineBuilder().build([film], research, [])

        self.assertEqual(set(timeline.views_json()), {"release"})
        self.assertEqual(timeline.valid_edges, [])

    def test_guide_validator_rejects_citation_owned_by_another_film(self):
        film = sample_film()
        evidence = sample_evidence(uuid4())
        claim = Claim(title="Reason", text="Claim", evidence_ids=[evidence.id])
        guide = Guide(
            title="Guide", introduction="Introduction", common_primer=[],
            film_guides=[FilmGuide(film_id=film.id, reason=claim, primer=[], background=[])],
            viewing_order=[film.id], discussion_questions=[],
        )

        with self.assertRaisesRegex(ValueError, "does not belong"):
            GuideValidator().validate(guide, [film], [evidence])

    async def test_assessment_accepts_cited_shared_non_metadata_evidence(self):
        film = sample_film()
        evidence = sample_evidence(None)
        assessment = SimpleNamespace(decisions=[SimpleNamespace(
            film_id=film.id,
            evidence_ids=[evidence.id],
            model_dump=lambda: {
                "film_id": film.id,
                "status": DecisionStatus.ACCEPTED,
                "reason": "A shared source explicitly names the film and topic.",
                "relevance": "primary",
                "evidence_ids": [evidence.id],
                "next_query": "",
            },
        )])

        class Operations:
            async def logged(self, ctx, operation, action):
                return assessment

        ctx = context(CurationState.ASSESS)
        ctx.evidence = [evidence]
        result = await AssessNode(Operations())._assess(ctx, [film])

        self.assertEqual(result[film.id].status, DecisionStatus.ACCEPTED)

    async def test_assessment_replaces_over_specific_follow_up_when_evidence_is_missing(self):
        film = sample_film()
        metadata = sample_evidence(film.id).model_copy(update={"kind": EvidenceKind.METADATA})
        assessment = SimpleNamespace(decisions=[SimpleNamespace(
            film_id=film.id,
            evidence_ids=[metadata.id],
            model_dump=lambda: {
                "film_id": film.id,
                "status": DecisionStatus.NEEDS_EVIDENCE,
                "reason": "A non-metadata source is required.",
                "relevance": "primary",
                "evidence_ids": [metadata.id],
                "next_query": "very restrictive scholarly institution query",
            },
        )])

        class Operations:
            async def logged(self, ctx, operation, action):
                return assessment

        ctx = context(CurationState.ASSESS)
        ctx.constraints.theme = "state surveillance"
        ctx.evidence = [metadata]
        result = await AssessNode(Operations())._assess(ctx, [film])

        self.assertEqual(
            result[film.id].next_query,
            f'"{film.original_title}" {film.year} film state surveillance',
        )

    def test_guide_draft_resolves_numbered_citations_and_omits_invalid_optional_notes(self):
        film = sample_film()
        evidence = sample_evidence(film.id)
        reason = {"title": "Reason", "text": "Based on the film source", "sources": [1]}
        draft = GuideDraft.model_validate({
            "title": "Guide", "introduction": "Introduction", "viewing_order": [1],
            "film_guides": [{"film": 1, "reason": reason, "primer": [
                "Malformed optional note", {**reason, "sources": [2]}, reason,
            ]}],
        })

        guide = draft.resolve([film], [evidence])

        self.assertEqual(guide.film_guides[0].reason.evidence_ids, [evidence.id])
        self.assertEqual(len(guide.film_guides[0].primer), 1)
        self.assertEqual(guide.film_guides[0].background, [])
        GuideValidator().validate(guide, [film], [evidence])
        with self.assertRaisesRegex(ValueError, "use sources listed for this film"):
            draft.resolve([film], [sample_evidence(uuid4())])

    def test_guide_draft_accepts_shared_topic_evidence_but_not_metadata_only_reasons(self):
        film = sample_film()
        source = sample_evidence(None)
        draft = GuideDraft.model_validate({
            "title": "Guide", "introduction": "Introduction", "viewing_order": [1],
            "film_guides": [{"film": 1, "reason": {
                "title": "Reason", "text": "A shared article connects this film to the topic.", "sources": [1],
            }}],
        })

        guide = draft.resolve([film], [source])

        GuideValidator().validate(guide, [film], [source])
        self.assertEqual(guide.film_guides[0].reason.evidence_ids, [source.id])
        with self.assertRaisesRegex(ValueError, "non-metadata source"):
            draft.resolve([film], [source.model_copy(update={"kind": EvidenceKind.METADATA})])

    async def test_film_readings_close_database_scope_before_searching_and_check_run_ownership(self):
        film = sample_film()
        run_id = uuid4()
        readings = SimpleNamespace(for_film=AsyncMock(return_value=[]))
        runs = SimpleNamespace(get_film=AsyncMock(return_value={"film": film.model_dump()}))
        scope = SimpleNamespace(resolve=AsyncMock(return_value=runs))
        manager = AsyncMock()
        manager.__aenter__.return_value = scope
        factory = SimpleNamespace(create_scope=lambda: manager)

        async def search(selected):
            manager.__aexit__.assert_awaited_once()
            self.assertEqual(selected.id, film.id)
            return []

        readings.for_film.side_effect = search
        service = FilmReadingsApplicationService(factory, readings)
        self.assertEqual(await service.get(run_id, film.id), {"readings": []})
        runs.get_film.assert_awaited_once_with(run_id, film.id)

        readings.for_film.reset_mock()
        runs.get_film.side_effect = NotFoundError("This film is outside the selected list.")
        with self.assertRaises(NotFoundError):
            await service.get(run_id, film.id)
        readings.for_film.assert_not_awaited()

    async def test_exact_story_year_needs_a_film_source_with_an_explicit_quote(self):
        ctx = context(CurationState.ASSESS)
        ctx.constraints.story_period = "1403, Kingdom of Bohemia"
        film = sample_film()
        source = sample_evidence(film.id).model_copy(update={"body": "The film is set in Bohemia in 1402."})
        ctx.evidence = [source]
        decision = {"film_id": film.id, "status": "accepted", "reason": "Same period",
                    "evidence_ids": [source.id], "setting_quote": "The film is set in Bohemia in 1403."}
        operations = SimpleNamespace(logged=AsyncMock(return_value=Assessment(decisions=[decision])))
        node = AssessNode(operations)
        result = (await node._assess(ctx, [film]))[film.id]
        self.assertEqual(result.status, DecisionStatus.NEEDS_EVIDENCE)
        self.assertIn("1403", result.next_query)

        ctx.evidence = [source.model_copy(update={"body": decision["setting_quote"]})]
        self.assertEqual((await node._assess(ctx, [film]))[film.id].status, DecisionStatus.ACCEPTED)
        ctx.constraints.setting_match = "nearby"
        ctx.evidence = [source]
        nearby = (await node._assess(ctx, [film]))[film.id]
        self.assertEqual(nearby.status, DecisionStatus.ACCEPTED)
        self.assertEqual(nearby.relevance.value, "secondary")

        ctx.constraints.setting_match = "strict"
        ctx.evidence = [source.model_copy(update={"film_id": None, "body": decision["setting_quote"]})]
        self.assertEqual((await node._assess(ctx, [film]))[film.id].status, DecisionStatus.NEEDS_EVIDENCE)

    def test_transition_requires_exactly_one_match(self):
        ctx = context()
        ctx.outcome = NodeOutcome.SUCCESS
        with self.assertRaisesRegex(WorkflowTransitionError, "no transition"):
            TransitionEngine([]).next_state(ctx)

        duplicate = WorkflowTransition(CurationState.PLAN, NodeOutcome.SUCCESS, CurationState.DISCOVER)
        with self.assertRaisesRegex(WorkflowTransitionError, "ambiguous transition"):
            TransitionEngine([duplicate, duplicate]).next_state(ctx)

    async def test_runner_requires_an_outcome(self):
        with self.assertRaisesRegex(WorkflowRunnerError, "did not set an outcome"):
            with self.assertLogs("cinecontext.ai.workflow", level="ERROR"):
                await WorkflowRunner(
                    {CurationState.PLAN: OutcomeNode(None)}, TransitionEngine([])
                ).run(context())

    async def test_runner_requires_a_registered_node(self):
        with self.assertRaisesRegex(WorkflowRunnerError, "no node registered"):
            await WorkflowRunner({}, TransitionEngine([])).run(context())

    async def test_runner_enforces_step_limit(self):
        transition = WorkflowTransition(CurationState.PLAN, NodeOutcome.RETRY, CurationState.PLAN)
        with self.assertRaisesRegex(WorkflowRunnerError, "step limit"):
            await WorkflowRunner(
                {CurationState.PLAN: OutcomeNode(NodeOutcome.RETRY)},
                TransitionEngine([transition]), max_steps=2,
            ).run(context())

    async def test_follow_up_routes_back_through_assessment(self):
        transitions = TransitionEngine([
            WorkflowTransition(CurationState.ASSESS, NodeOutcome.FOLLOW_UP, CurationState.FOLLOW_UP),
            WorkflowTransition(CurationState.FOLLOW_UP, NodeOutcome.SUCCESS, CurationState.ASSESS),
            WorkflowTransition(CurationState.ASSESS, NodeOutcome.SUCCESS, CurationState.COMPLETED),
        ])
        calls = []

        class Assess:
            async def run(self, ctx):
                calls.append(ctx.state)
                ctx.outcome = NodeOutcome.FOLLOW_UP if calls.count(CurationState.ASSESS) == 1 else NodeOutcome.SUCCESS
                return ctx

        class FollowUp:
            async def run(self, ctx):
                calls.append(ctx.state)
                ctx.outcome = NodeOutcome.SUCCESS
                return ctx

        result = await WorkflowRunner(
            {CurationState.ASSESS: Assess(), CurationState.FOLLOW_UP: FollowUp()}, transitions
        ).run(context(CurationState.ASSESS))
        self.assertEqual(calls, [CurationState.ASSESS, CurationState.FOLLOW_UP, CurationState.ASSESS])
        self.assertEqual(result.state, CurationState.COMPLETED)

    async def test_runner_logs_node_and_transition_context(self):
        transitions = TransitionEngine([
            WorkflowTransition(CurationState.PLAN, NodeOutcome.SUCCESS, CurationState.COMPLETED)
        ])
        ctx = context()

        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        workflow_logger = logging.getLogger("cinecontext.ai.workflow")
        handler = Capture()
        workflow_logger.addHandler(handler)
        workflow_logger.setLevel(logging.DEBUG)
        try:
            await WorkflowRunner(
                {CurationState.PLAN: OutcomeNode(NodeOutcome.SUCCESS)}, transitions
            ).run(ctx)
        finally:
            workflow_logger.removeHandler(handler)

        output = "\n".join(record.getMessage() for record in records)
        self.assertIn("Node started", output)
        self.assertIn("Node completed", output)
        self.assertIn("next_state=completed", output)
        self.assertTrue(any(record.run_id == str(ctx.run_id) for record in records))

    async def test_guide_retry_is_a_new_engine_cycle(self):
        transitions = TransitionEngine([
            WorkflowTransition(CurationState.GUIDE, NodeOutcome.RETRY, CurationState.GUIDE),
            WorkflowTransition(CurationState.GUIDE, NodeOutcome.PARTIAL, CurationState.PUBLISH_PARTIAL),
            WorkflowTransition(CurationState.PUBLISH_PARTIAL, NodeOutcome.SUCCESS, CurationState.COMPLETED),
        ])
        attempts = 0

        class Guide:
            async def run(self, ctx):
                nonlocal attempts
                attempts += 1
                ctx.outcome = NodeOutcome.RETRY if attempts == 1 else NodeOutcome.PARTIAL
                return ctx

        result = await WorkflowRunner(
            {
                CurationState.GUIDE: Guide(),
                CurationState.PUBLISH_PARTIAL: OutcomeNode(NodeOutcome.SUCCESS),
            }, transitions,
        ).run(context(CurationState.GUIDE))
        self.assertEqual(attempts, 2)
        self.assertEqual(result.state, CurationState.COMPLETED)

    async def test_guide_retry_workflow_skips_prior_phases(self):
        from cinecontext.ai.nodes import create_guide_retry_workflow

        nodes, transitions = create_guide_retry_workflow(SimpleNamespace())

        self.assertEqual(
            set(nodes),
            {CurationState.GUIDE, CurationState.PUBLISH, CurationState.PUBLISH_PARTIAL},
        )
        self.assertFalse(any(rule.target == CurationState.RATINGS for rule in transitions.transitions))


class WebResearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_web_research_retries_a_temporary_proxy_failure(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ProxyError("503 Service Unavailable", request=request)
            return httpx.Response(200, json={"query": {"pages": [{
                "title": "History", "fullurl": "https://en.wikipedia.org/wiki/History",
                "extract": "Historical evidence",
            }]}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tools = create_research_tools(SimpleNamespace(), client, ToolsOptions(wikipedia_enabled=True))
            results = await tools.search_web("history")

        self.assertEqual(attempts, 2)
        self.assertEqual([item.body for item in results], ["Historical evidence"])

    def test_duckduckgo_parser_extracts_snippets_and_decodes_redirects(self):
        parser = DuckDuckGoHTMLParser()
        parser.feed('''
            <div class="result">
              <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ffilm">Film source</a>
              <a class="result__snippet">A useful <b>film</b> summary.</a>
            </div>
            <a class="result__a" href="https://other.example/page">Other source</a>
            <div class="result__snippet">Other summary</div>
        ''')
        parser.close()

        self.assertEqual(parser.results[0].url, "https://example.com/film")
        self.assertEqual(parser.results[0].title, "Film source")
        self.assertEqual(parser.results[0].snippet, "A useful film summary.")
        self.assertEqual(parser.results[1].url, "https://other.example/page")

    def test_html_extractor_ignores_invisible_content_and_normalizes_text(self):
        parser = VisibleHTMLParser()
        parser.feed("<title> Page  title </title><main>Hello\n world</main><script>bad()</script><style>hidden</style><svg>icon</svg><p>More text</p>")
        parser.close()

        self.assertEqual(parser.title, "Page title")
        self.assertEqual(parser.text, "Hello world More text")

    async def test_url_safety_rejects_unsafe_urls(self):
        public = lambda host: async_value({"93.184.216.34"})
        private = lambda host: async_value({"10.0.0.1"})

        self.assertFalse(await validate_public_url("file:///tmp/a", public))
        self.assertFalse(await validate_public_url("https://user:pass@example.com", public))
        self.assertFalse(await validate_public_url("http://localhost/test", public))
        self.assertFalse(await validate_public_url("http://10.0.0.1/test", private))
        self.assertTrue(await validate_public_url("https://example.com/test", public))

    async def test_duckduckgo_search_and_crawl_return_typed_evidence(self):
        film_id = uuid4()

        async def handler(request):
            if request.url.host == "html.duckduckgo.com":
                self.assertEqual(parse_qs(request.url.query.decode())["q"], ["test film"])
                return httpx.Response(200, headers={"content-type": "text/html"}, text='''
                    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fsource">Result title</a>
                    <div class="result__snippet">Search snippet</div>
                ''')
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<title>Source title</title><article>Page evidence text</article>")

        async def resolver(host):
            return {"93.184.216.34"}

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            evidence = await create_research_tools(
                SimpleNamespace(), client, ToolsOptions(), resolver=resolver
            ).search_web("test film", film_id)

        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].film_id, film_id)
        self.assertEqual(evidence[0].source_title, "Source title")
        self.assertEqual(evidence[0].source_url, "https://example.com/source")
        self.assertEqual(evidence[0].locator, "DuckDuckGo · Page excerpt")
        self.assertEqual(evidence[0].body, "Page evidence text")
        self.assertEqual(evidence[0].kind, EvidenceKind.WEB_EXCERPT)

    def test_default_tools_enable_free_web_search_without_brave(self):
        options = ToolsOptions()

        self.assertFalse(options.brave.enabled)
        self.assertTrue(options.duckduckgo.enabled)
        tools = create_research_tools(SimpleNamespace(), SimpleNamespace(), options)
        self.assertTrue(tools.web_enabled)

    async def test_brave_takes_precedence_over_duckduckgo(self):
        options = ToolsOptions.model_validate({"brave": {"api_key": "test-key"}})

        async def handler(request):
            self.assertEqual(request.url.host, "api.search.brave.com")
            return httpx.Response(200, json={"grounding": {"generic": []}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            evidence = await create_research_tools(
                SimpleNamespace(), client, options
            ).search_web("test film")

        self.assertEqual(evidence, [])


class CandidateDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_candidates_get_slots_and_catalog_aliases_keep_local_evidence(self):
        local = [sample_film().model_copy(update={
            "id": uuid4(), "title": f"Local {index}", "original_title": f"Original {index}",
        }) for index in range(10)]
        alias = local[0].model_copy(update={"id": uuid4(), "title": "Alternate title", "tmdb_id": 15})
        external = local[0].model_copy(update={
            "id": uuid4(), "title": "External film", "original_title": "External film", "tmdb_id": 16,
        })
        tools = SimpleNamespace(
            discover_editorial=AsyncMock(return_value=[FilmHint(title=alias.title)]),
            discover_catalog=AsyncMock(return_value=[FilmHint(title=external.title)]),
            lookup_movie=AsyncMock(side_effect=[alias, external]),
        )
        ctx = context(CurationState.DISCOVER)
        ctx.films = local[:6]
        ctx.plan = Plan(query="test", discovery_terms=["test"])
        ctx.tools = tools
        operations = WorkflowOperations(SimpleNamespace(record_tool_call=AsyncMock(), advance_run=AsyncMock()))

        await DiscoverNode(operations)._metadata_candidates(ctx, local[6:])

        self.assertEqual(len(ctx.films), 10)
        self.assertIn(local[0], ctx.films)
        self.assertIn(external, ctx.films)
        self.assertNotIn(alias, ctx.films)


    async def test_catalog_uses_keywords_and_release_bounds_without_story_dates(self):
        async def handler(request):
            params = request.url.params
            if request.url.path.endswith("/search/keyword"):
                if params["query"] == "unavailable":
                    return httpx.Response(503)
                if params["query"] == "bohemia":
                    return httpx.Response(200, json={"results": []})
                self.assertEqual(params["query"], "middle ages")
                return httpx.Response(200, json={"results": [
                    {"id": 1, "name": "middle ages"},
                    {"id": 2, "name": "middle ages (476-1453)"},
                    {"id": 3, "name": "middle-aged"},
                ]})
            self.assertTrue(request.url.path.endswith("/discover/movie"))
            self.assertEqual(params["with_keywords"], "1|2")
            self.assertEqual(params["primary_release_date.gte"], "1950-01-01")
            self.assertEqual(params["primary_release_date.lte"], "1969-12-31")
            self.assertNotIn("1403", str(request.url))
            return httpx.Response(200, json={"results": [
                {"title": "Marketa Lazarová", "release_date": "1967-11-24"},
                {"title": "Unreleased", "release_date": None},
            ]})

        constraints = Constraints(topic="Medieval film", theme="Bohemia", story_period="1403 Bohemia",
                                  release_from=1950, release_to=1969)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            provider = TmdbMetadataProvider(None, http, TmdbOptions(access_token="test"), None, None)
            leads = await provider.discover(["middle ages", "unavailable"], constraints)
        self.assertEqual([(film.title, film.year) for film in leads], [("Marketa Lazarová", 1967)])

    async def test_exact_theme_tag_prevents_generic_mood_candidates(self):
        async def handler(request):
            params = request.url.params
            if request.url.path.endswith("/search/keyword"):
                keyword = params["query"]
                return httpx.Response(200, json={"results": [
                    {"id": {"norse mythology": 1, "revenge": 2}[keyword], "name": keyword}
                ]})
            self.assertEqual(params["with_keywords"], "1")
            return httpx.Response(200, json={"results": [
                {"title": "The Northman", "release_date": "2022-04-07"}
            ]})

        constraints = Constraints(topic="Five dark Norse films", theme="Norse mythology, revenge")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            provider = TmdbMetadataProvider(None, http, TmdbOptions(access_token="test"), None, None)
            leads = await provider.discover(["revenge"], constraints)
        self.assertEqual([film.title for film in leads], ["The Northman"])

    def test_regional_editorial_leads_respect_confirmed_release_range(self):
        criteria = Constraints(topic="Romanian political memory", theme="surveillance",
                               story_period="Romania", release_from=2007, release_to=2020)
        self.assertEqual([film.title for film in curated_candidates(criteria, [])], ["Police, Adjective"])

    async def test_tmdb_hint_can_use_a_verified_premiere_year(self):
        async def handler(request):
            if request.url.path.endswith("/search/movie"):
                return httpx.Response(200, json={"results": [
                    {"id": 37861, "title": "Valhalla Rising", "release_date": "2010-03-10"},
                ]})
            return httpx.Response(200, json={
                "release_date": "2010-03-10",
                "release_dates": {"results": [{"release_dates": [
                    {"type": 1, "release_date": "2009-09-04T00:00:00.000Z"},
                ]}]},
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            provider = TmdbMetadataProvider(None, http, TmdbOptions(access_token="test"), None, None)
            details = await provider._lookup_details(FilmHint(title="Valhalla Rising", year=2009))
            invalid = await provider._lookup_details(FilmHint(title="Valhalla Rising", year=2008))
        self.assertEqual(provider._catalog_release(details), ("2009-09-04", "premiere"))
        self.assertIsNone(invalid)

    async def test_tmdb_matches_accented_titles_for_existing_films(self):
        async def handler(request):
            if request.url.path.endswith("/search/movie"):
                return httpx.Response(200, json={"results": [
                    {"id": 67404, "title": "The Autobiography of Nicolae Ceausescu", "release_date": "2010-05-18"},
                ]})
            self.assertTrue(request.url.path.endswith("/movie/67404"))
            return httpx.Response(200, json={"poster_path": "/correct.jpg", "release_date": "2010-05-18"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            provider = TmdbMetadataProvider(None, http, TmdbOptions(access_token="test"), None, None)
            details = await provider._lookup_details(FilmHint(title="The Autobiography of Nicolae Ceaușescu", year=2010))
        self.assertEqual(details["poster_path"], "/correct.jpg")

    async def test_editorial_links_use_verified_title_and_release_identity(self):
        constraints = Constraints(topic="Medieval film", theme="Feudal power",
                                  story_period="1403 Bohemia", release_from=1956, release_to=2000)
        self.assertEqual([(film.title, film.year) for film in curated_candidates(constraints, [])], [
            ("Jan Žižka", 1956), ("Marketa Lazarová", 1967),
        ])
        film = sample_film().model_copy(update={
            "title": "Andrei Rublev", "original_title": "Андрей Рублёв", "year": 1966,
        })
        service = WebFilmReadings(Options(ToolsOptions(duckduckgo={"enabled": False})))
        readings = await service.for_film(film)
        self.assertEqual({reading.publication for reading in readings},
                         {"BFI", "Criterion", "Senses of Cinema"})
        self.assertTrue(all(reading.summary for reading in readings))
        self.assertEqual(await service.for_film(film.model_copy(update={"year": 2018})), [])
        self.assertFalse(article_url("https://criterion.com.example/current/posts/42-film", "criterion.com"))
        self.assertFalse(article_url("https://criterion.com/films/42-film", "criterion.com"))

    async def test_midway_readings_match_the_correct_film(self):
        service = WebFilmReadings(Options(ToolsOptions(duckduckgo={"enabled": False})))
        film = sample_film().model_copy(update={"title": "Midway", "original_title": "Midway"})
        for year, author in ((1976, "Roger Ebert"), (2019, "Odie Henderson")):
            with self.subTest(year=year):
                readings = await service.for_film(film.model_copy(update={"year": year}))
                self.assertEqual([(row.title, row.author) for row in readings], [(f"Midway ({year})", author)])


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    def test_sanitize_message_redacts_secrets_and_truncates(self):
        message = (
            "Authorization: Bearer abc.def api_key=super-secret password=hunter2 "
            "https://user:pass@example.com/path token=another-secret " + "x" * 200
        )

        result = sanitize_message(message, 140)

        for secret in ("abc.def", "super-secret", "hunter2", "user:pass", "another-secret"):
            self.assertNotIn(secret, result)
        self.assertLessEqual(len(result), 140)
        self.assertTrue(result.endswith("..."))

    def test_configure_logging_is_idempotent_and_writes_file(self):
        logger = logging.getLogger("cinecontext")
        existing = list(logger.handlers)
        try:
            with tempfile.TemporaryDirectory() as directory:
                options = LoggingOptions(
                    console_enabled=False, file_enabled=True, path="logs/test.log",
                    max_bytes=10000, backup_count=1,
                )
                configure_logging(options, directory)
                configure_logging(options, directory)
                logger.info("test event")
                for handler in logger.handlers:
                    handler.flush()

                self.assertEqual(len(logger.handlers), 1)
                content = (Path(directory) / "logs" / "test.log").read_text(encoding="utf-8")
                self.assertIn("test event", content)
                self.assertIn("run_id=-", content)
                for handler in list(logger.handlers):
                    if getattr(handler, "_cinecontext_handler", False):
                        logger.removeHandler(handler)
                        handler.close()
        finally:
            for handler in list(logger.handlers):
                if getattr(handler, "_cinecontext_handler", False):
                    logger.removeHandler(handler)
                    handler.close()
            for handler in existing:
                if handler not in logger.handlers:
                    logger.addHandler(handler)

    async def test_failed_operation_persists_safe_diagnostic_detail(self):
        class Persistence:
            def __init__(self):
                self.calls = []

            async def record_tool_call(self, *args):
                self.calls.append(args)

        persistence = Persistence()
        ctx = context(CurationState.DISCOVER)

        async def fail():
            raise ValueError("api_key=do-not-store invalid response")

        with self.assertLogs("cinecontext.ai.nodes", level="ERROR"):
            with self.assertRaises(ValueError):
                await WorkflowOperations(persistence).logged(
                    ctx, OperationName.SEARCH_WEB, fail, {"caller": "test"}
                )

        detail = persistence.calls[0][-1]
        self.assertEqual(detail["error_type"], "ValueError")
        self.assertEqual(detail["state"], "discover")
        self.assertEqual(detail["caller"], "test")
        self.assertIn("[REDACTED]", detail["error_message"])
        self.assertNotIn("do-not-store", detail["error_message"])

    async def test_curation_failure_persists_diagnostic_id_and_technical_error(self):
        class Persistence:
            def __init__(self):
                self.finished = None

            async def finish_run(self, *args):
                self.finished = args

        class Provider:
            mode = SimpleNamespace(value="mock")
            model_id = "safe-provider"

            def create(self):
                raise RuntimeError("provider failed token=do-not-store")

        persistence = Persistence()
        service = CurationService(
            persistence, Provider(), Options(ToolsOptions()),
            Options(LoggingOptions(include_technical_failure_detail=True)),
        )

        with self.assertLogs("cinecontext.ai.curation", level="ERROR"):
            await service.run(uuid4(), Constraints(topic="test", theme="test", count=1))

        _, status, message = persistence.finished
        self.assertEqual(status, RunStatus.FAILED)
        self.assertRegex(message, r"Diagnostic [0-9a-f-]{36}: RuntimeError: provider failed")
        self.assertIn("[REDACTED]", message)
        self.assertNotIn("do-not-store", message)


class StructuredModelOutputTests(unittest.IsolatedAsyncioTestCase):
    class Result(BaseModel):
        value: str

    async def test_assessment_keeps_supported_decisions_when_model_mixes_valid_and_invalid_citations(self):
        first, second = sample_film(), sample_film()
        first_source = sample_evidence(first.id)
        unrelated_source = sample_evidence(uuid4())
        malformed_uuid = "68eba5c0-daff-48b1-9ba0-0103200c920bb"
        model = OpenAIModel(ModelOptions(api_key="test", model_id="test"), LoggingOptions())
        ctx = context(CurationState.ASSESS)
        ctx.model = model
        ctx.evidence = [first_source, unrelated_source]

        async def reply(system, payload, schema):
            request = json.loads(payload)
            self.assertEqual([film["film"] for film in request["films"]], [1, 2])
            self.assertEqual([(source["source"], source["film"]) for source in request["evidence"]], [(1, 1)])
            self.assertNotIn(str(first_source.id), payload)
            return ModelResponse(json.dumps({"decisions": [
                {"film": 1, "status": "accepted", "reason": "Fits", "sources": [1, malformed_uuid, 700, 1]},
                {"film": 2, "status": "accepted", "reason": "Unverified", "sources": [malformed_uuid, 1, 700]},
            ]}), "stop")

        async def logged(ctx, operation, action):
            return await action()

        model._chat_complete = AsyncMock(side_effect=reply)
        try:
            decisions = await AssessNode(SimpleNamespace(logged=logged))._assess(ctx, [first, second])
            model._chat_complete.assert_awaited_once()
            self.assertEqual(decisions[first.id].status, DecisionStatus.ACCEPTED)
            self.assertEqual(decisions[first.id].evidence_ids, [first_source.id])
            self.assertEqual(decisions[second.id].status, DecisionStatus.NEEDS_EVIDENCE)
            self.assertEqual(decisions[second.id].evidence_ids, [])
            self.assertTrue(decisions[second.id].next_query)
        finally:
            await model.close()

    async def test_invalid_json_is_retried_then_succeeds(self):
        model = OpenAIModel(ModelOptions(
            api_key="test", model_id="test", structured_output_retries=1,
        ), LoggingOptions(capture_model_responses=False))
        responses = iter([
            ('{"value":"truncated"', "length"),
            ('{"value":"complete"}', "stop"),
        ])

        async def complete(system, payload, schema):
            content, finish_reason = next(responses)
            return ModelResponse(content, finish_reason)

        model._chat_complete = complete
        try:
            with self.assertLogs("cinecontext.ai.openai_model", level="WARNING") as logs:
                result = await model.complete(self.Result, "Return data.", {})
            self.assertEqual(result.value, "complete")
            self.assertTrue(any("finish_reason=length" in item for item in logs.output))
        finally:
            await model.close()

    async def test_exhausted_validation_raises_safe_error_without_response_body(self):
        model = OpenAIModel(ModelOptions(
            api_key="test", model_id="test", structured_output_retries=0,
        ), LoggingOptions(capture_model_responses=False))

        async def complete(system, payload, schema):
            return ModelResponse('{"value":"sensitive-source-text"', "length")

        model._chat_complete = complete
        try:
            with self.assertLogs("cinecontext.ai.openai_model", level="ERROR"):
                with self.assertRaises(ModelOutputValidationError) as caught:
                    await model.complete(self.Result, "Return data.", {})
            self.assertIn("finish_reason=length", str(caught.exception))
            self.assertNotIn("sensitive-source-text", str(caught.exception))
        finally:
            await model.close()

    async def test_raw_response_capture_is_isolated_and_records_finish_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            model = OpenAIModel(
                ModelOptions(api_key="test", model_id="test", structured_output_retries=0),
                LoggingOptions(
                    capture_model_responses=True,
                    model_response_path=directory,
                ),
            )
            model.set_run_id(uuid4())

            async def complete(system, payload, schema):
                return ModelResponse('{"value":"captured"}', "stop", "hidden reasoning")

            model._chat_complete = complete
            try:
                await model.complete(self.Result, "Return data.", {})
                files = list(Path(directory).glob("*.txt"))
                self.assertEqual(len(files), 1)
                content = files[0].read_text(encoding="utf-8")
                self.assertIn("finish_reason=stop", content)
                self.assertIn("reasoning_length=16", content)
                self.assertIn('{"value":"captured"}', content)
                self.assertIn("hidden reasoning", content)
            finally:
                await model.close()


class TaccModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_job_clock_transitions_from_queue_to_running_allocation(self):
        transport = TaccSdkTransport("tcloud", "42", 30)
        transport.command = AsyncMock(side_effect=[
            b"42 tacc qwen demo PD 0:00 1 (Resources)\n",
            b"42 tacc qwen demo R 4:20 1 node\n",
            b"qwen3.8-27b\n",
        ])
        with tempfile.TemporaryDirectory() as directory:
            records = Path(directory)
            (records / "42").write_text("1000 01:00:00\n", encoding="utf-8")
            with patch("cinecontext.ai.tacc_transport.JOB_RECORDS", records), patch(
                "cinecontext.ai.tacc_transport.time", return_value=1240,
            ):
                queued = await transport.readiness(include_timing=True)
                ready = await transport.readiness(include_timing=True)
        self.assertEqual((queued.status, queued.queued_seconds, queued.remaining_seconds), ("queued", 240, None))
        self.assertEqual((ready.status, ready.queued_seconds, ready.remaining_seconds), ("ready", None, 3340))

    async def test_sdk_serializes_qwen_options_and_returns_parsed_result(self):
        jobs = [
            b"JOBID PARTITION NAME USER ST\n42 tacc qwen demo R\n",
            b"qwen3.8-27b\n",
        ]

        async def command(operation, *args, timeout=20):
            if jobs:
                return jobs.pop(0)
            if operation == "upload":
                self.assertEqual(args[1], "cinecontext-course-kit/e2e-42/")
                body = json.loads(Path(args[0]).read_bytes())
                self.assertEqual(body["model"], "qwen3.8-27b")
                self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
                self.assertEqual(body["max_tokens"], 1000)
                return b"uploaded"
            self.assertTrue(args[0].endswith(".response.json"))
            content = {"constraints": {"topic": "Test", "theme": "History"}, "clarification": ""}
            body = {"choices": [{"message": {"content": json.dumps(content)}, "finish_reason": "stop"}]}
            return json.dumps({"status": 200, "body": body}).encode()

        with patch.object(TaccSdkTransport, "command", side_effect=command):
            model = OpenAIModel(ModelOptions(mode="tacc", tacc_job_id=42), LoggingOptions())
            try:
                result = await model.parse("Test")
                self.assertIsInstance(result, ParsedRequest)
                self.assertEqual(result.constraints.theme, "History")
            finally:
                await model.close()

    async def test_running_job_tolerates_a_failed_ready_marker_read(self):
        transport = TaccSdkTransport("tcloud", "42", 30)
        transport.command = AsyncMock(side_effect=[b"42 tacc qwen demo R 4:20 1 node", None])
        transport.exchange = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
        request = httpx.Request("POST", "http://tacc.invalid/v1/chat/completions", json={})

        response = await transport.handle_async_request(request)

        self.assertEqual(response.status_code, 200)
        transport.exchange.assert_awaited_once_with(request)

    async def test_readiness_distinguishes_a_missing_marker_from_a_failed_status_check(self):
        transport = TaccSdkTransport("tcloud", "42", 30)
        running = b"42 tacc qwen demo R 4:20 1 node"
        transport.command = AsyncMock(side_effect=[running, b"", running, None])
        self.assertEqual((await transport.readiness()).status, "starting")
        self.assertEqual((await transport.readiness()).status, "unavailable")

    async def test_queued_job_does_not_upload_request(self):
        transport = TaccSdkTransport("tcloud", "42", 30)
        transport.command = AsyncMock(return_value=b"JOBID PARTITION NAME USER ST\n42 tacc qwen demo PD\n")
        request = httpx.Request("POST", "http://tacc.invalid/v1/chat/completions", json={})
        response = await transport.handle_async_request(request)
        self.assertEqual(response.status_code, 503)
        transport.command.assert_awaited_once_with("ps", "-j", "42", timeout=6)


class TaccApiTests(unittest.TestCase):
    def test_production_composition_and_local_job_submission_boundary(self):
        configuration = {
            "APP_ENVIRONMENT": "tacc", "APP__MODEL__MODE": "tacc",
            "APP__MODEL__TACC_JOB_ID": "42", "APP__MODEL__BASE_URL": "",
            "APP__LOGGING__FILE_ENABLED": "false", "APP__LOGGING__CONSOLE_ENABLED": "false",
        }
        with patch.dict(os.environ, configuration), patch.object(
            TaccSdkTransport, "readiness", new_callable=AsyncMock,
            return_value=ModelReadiness("stopped", "42"),
        ), patch.object(TaccJobs, "submit_after", new_callable=AsyncMock, return_value="43") as submit:
            app = create_app(database_url="postgresql+asyncpg://unused@127.0.0.1/unused", serve_frontend=False)
            # These endpoints do not access the database; skip database startup intentionally.
            client = TestClient(app, base_url="http://127.0.0.1:8766", client=("127.0.0.1", 50000))
            try:
                health = client.get("/api/health")
                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.json()["model_status"], "stopped")
                self.assertTrue(health.json()["tacc_resubmit_enabled"])
                body = {"previous_job_id": "42", "restart": True}
                with self.assertLogs(level="ERROR"):
                    blocked = client.post("/api/tacc/jobs", json=body, headers={"Origin": "https://example.com"})
                self.assertEqual(blocked.status_code, 403)
                submit.assert_not_awaited()
                response = client.post("/api/tacc/jobs", json=body, headers={"Origin": "http://127.0.0.1:8766"})
                self.assertEqual(response.status_code, 202)
                self.assertEqual(response.json(), {"job_id": "43"})
                submit.assert_awaited_once_with("42", restart=True)
                remote = TestClient(app, base_url="http://127.0.0.1:8766", client=("192.0.2.1", 50000))
                try:
                    with self.assertLogs(level="ERROR"):
                        blocked = remote.post("/api/tacc/jobs", json=body)
                    self.assertEqual(blocked.status_code, 403)
                    self.assertFalse(remote.get("/api/health").json()["tacc_resubmit_enabled"])
                finally:
                    remote.close()
            finally:
                client.close()
                asyncio.run(app.state.alcedo.provider.close())
                asyncio.run(app.state.alcedo.engine.dispose())


class TaccRestartTests(unittest.IsolatedAsyncioTestCase):
    async def test_excluded_node_submission_packages_the_service_supervisor(self):
        jobs = TaccJobs(Options(ModelOptions(mode="tacc", tacc_job_id="42")))

        async def submit(command, *, timeout, cwd):
            self.assertEqual(command, "submit")
            self.assertEqual(timeout, 170)
            self.assertEqual({path.name for path in cwd.iterdir()}, {
                "job.sh", "worker.py", "service_supervisor.py", "tuxiv.conf",
            })
            self.assertIn("    - exclude=node-1,node-2\n", (cwd / "tuxiv.conf").read_text())
            return b"Submitted batch job 43\n"

        with patch.dict(os.environ, {"CINECONTEXT_TACC_EXCLUDE_NODES": "node-1,node-2"}):
            self.assertEqual(
                await jobs._submit_configured_job(SimpleNamespace(command=submit)),
                b"Submitted batch job 43\n",
            )

    async def test_restart_is_shared_by_concurrent_requests_and_updates_the_saved_job(self):
        jobs = TaccJobs(Options(ModelOptions(mode="tacc", tacc_job_id="42")))
        transport = SimpleNamespace(
            job_state=AsyncMock(side_effect=[("starting", 120), ("stopped", None)]),
            command=AsyncMock(return_value=b""),
        )
        jobs._submit_configured_job = AsyncMock(return_value=b"Submitted batch job 43\n")
        with tempfile.TemporaryDirectory() as folder, patch(
            "cinecontext.infrastructure.tacc_jobs.JOB_RECORDS", Path(folder)
        ), patch("cinecontext.infrastructure.tacc_jobs.TaccSdkTransport", return_value=transport):
            first, second = await asyncio.gather(
                jobs.submit_after("42", restart=True), jobs.submit_after("42", restart=True),
            )
            self.assertEqual((first, second), ("43", "43"))
            self.assertEqual(jobs.job_id, "43")
            self.assertEqual((Path(folder) / "latest").read_text(), "43\n")
            self.assertIn("01:00:00", (Path(folder) / "43").read_text())
            self.assertEqual(await jobs.submit_after("42", restart=True), "43")
        transport.command.assert_awaited_once_with("cancel", "-j", "42", timeout=12)
        jobs._submit_configured_job.assert_awaited_once()

    async def test_regular_submit_never_cancels_an_active_job(self):
        jobs = TaccJobs(Options(ModelOptions(mode="tacc", tacc_job_id="42")))
        transport = SimpleNamespace(
            job_state=AsyncMock(return_value=("starting", 100)), command=AsyncMock(),
        )
        jobs._submit_configured_job = AsyncMock()
        with patch("cinecontext.infrastructure.tacc_jobs.TaccSdkTransport", return_value=transport):
            with self.assertRaises(ConflictError):
                await jobs.submit_after("42")
        transport.command.assert_not_awaited()
        jobs._submit_configured_job.assert_not_awaited()

    async def test_unconfirmed_cancellation_does_not_submit_another_job(self):
        jobs = TaccJobs(Options(ModelOptions(mode="tacc", tacc_job_id="42")))
        transport = SimpleNamespace(
            job_state=AsyncMock(side_effect=[("starting", 100), ("unavailable", None)]),
            command=AsyncMock(return_value=None),
        )
        jobs._submit_configured_job = AsyncMock()
        with patch("cinecontext.infrastructure.tacc_jobs.TaccSdkTransport", return_value=transport):
            with self.assertRaises(ServiceUnavailableError):
                await jobs.submit_after("42", restart=True)
        self.assertEqual(jobs.job_id, "42")
        jobs._submit_configured_job.assert_not_awaited()

    async def test_submission_failure_can_be_retried_after_cancellation(self):
        jobs = TaccJobs(Options(ModelOptions(mode="tacc", tacc_job_id="42")))
        transport = SimpleNamespace(
            job_state=AsyncMock(side_effect=[("queued", None), ("stopped", None), ("stopped", None)]),
            command=AsyncMock(return_value=b""),
        )
        jobs._submit_configured_job = AsyncMock(side_effect=[None, b"Submitted batch job 43\n"])
        with tempfile.TemporaryDirectory() as folder, patch(
            "cinecontext.infrastructure.tacc_jobs.JOB_RECORDS", Path(folder)
        ), patch("cinecontext.infrastructure.tacc_jobs.TaccSdkTransport", return_value=transport):
            with self.assertRaises(ServiceUnavailableError):
                await jobs.submit_after("42", restart=True)
            self.assertEqual(await jobs.submit_after("42"), "43")
        transport.command.assert_awaited_once()


class SpaStaticFilesTests(unittest.TestCase):
    def test_client_route_falls_back_to_index_but_missing_asset_stays_404(self):
        from fastapi import FastAPI

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("<html>SPA shell</html>", encoding="utf-8")
            (root / "asset.js").write_text("console.log('asset')", encoding="utf-8")
            app = FastAPI()
            app.mount("/", SpaStaticFiles(root))

            with TestClient(app) as client:
                route = client.get("/research/abc/progress", headers={"Accept": "text/html"})
                asset = client.get("/asset.js")
                missing_asset = client.get("/missing.js", headers={"Accept": "text/html"})
                api_like = client.get("/api/missing", headers={"Accept": "application/json"})

            self.assertEqual(route.status_code, 200)
            self.assertIn("SPA shell", route.text)
            self.assertEqual(asset.status_code, 200)
            self.assertIn("console.log", asset.text)
            self.assertEqual(missing_asset.status_code, 404)
            self.assertEqual(api_like.status_code, 404)

class FakeRunRepository:
    def __init__(self, run=None):
        self.run = run
        self.deleted = []

    async def get_for_update(self, run_id):
        return self.run

    async def delete_with_data(self, run_id):
        self.deleted.append(run_id)


class FakeUnitOfWork:
    async def execute(self, action, *, isolation_level=None):
        return await action()


class FilmDetailTests(unittest.IsolatedAsyncioTestCase):
    async def test_details_keep_shared_evidence_with_omitted_film_id(self):
        film_id, other_id, run_id = uuid4(), uuid4(), uuid4()
        film = {"id": str(film_id), "title": "Selected film"}
        shared = {"id": "shared"}
        own = {"id": "own", "film_id": str(film_id)}
        other = {"id": "other", "film_id": str(other_id)}
        run = SimpleNamespace(
            study_result={"films": [film], "evidence": [shared, own, other]}, guide=None,
        )
        films = SimpleNamespace(get_posters=AsyncMock(return_value={str(film_id): "/poster.jpg"}))
        service = RunApplicationService(
            SimpleNamespace(get=AsyncMock(return_value=run)), films, None, SimpleNamespace(mode="tacc"),
        )
        detail = await service.get_film(run_id, film_id)
        self.assertEqual(detail["evidence"], [shared, own])
        self.assertEqual(detail["film"]["poster_path"], "/poster.jpg")


class RunDeletionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def service(repository):
        return RunApplicationService(
            repository,
            SimpleNamespace(),
            FakeUnitOfWork(),
            SimpleNamespace(mode="mock"),
        )

    async def test_delete_removes_terminal_run(self):
        run = CurationRun.start(uuid4(), {"topic": "test"}, "mock")
        run.status = RunStatus.COMPLETED
        repository = FakeRunRepository(run)

        await self.service(repository).delete(run.id)

        self.assertEqual(repository.deleted, [run.id])

    async def test_delete_rejects_active_run(self):
        run = CurationRun.start(uuid4(), {"topic": "test"}, "mock")
        repository = FakeRunRepository(run)

        with self.assertRaises(ConflictError):
            await self.service(repository).delete(run.id)
        self.assertEqual(repository.deleted, [])

    async def test_delete_reports_missing_run(self):
        with self.assertRaises(NotFoundError):
            await self.service(FakeRunRepository()).delete(uuid4())


class GuideRetryClaimTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def service(repository):
        return RunApplicationService(
            repository,
            SimpleNamespace(),
            FakeUnitOfWork(),
            SimpleNamespace(mode="mock"),
        )

    @staticmethod
    def retryable_run(status=RunStatus.PARTIAL):
        film = sample_film()
        evidence = sample_evidence(film.id)
        run = CurationRun.start(
            uuid4(), Constraints(topic="test", theme="test").model_dump(mode="json"), "mock"
        )
        run.status = status
        run.stage = "finished"
        run.error = "old error"
        run.study_result = {
            "films": [film.model_dump(mode="json")],
            "evidence": [evidence.model_dump(mode="json")],
            "decisions": [],
            "limitations": [],
            "follow_ups": 0,
        }
        return run

    async def test_retry_claim_hydrates_snapshot_and_reactivates_run(self):
        run = self.retryable_run()

        snapshot = await self.service(FakeRunRepository(run)).retry_guide(run.id)

        self.assertIsInstance(snapshot, GuideRetrySnapshot)
        self.assertEqual(snapshot.films[0].id, UUID(run.study_result["films"][0]["id"]))
        self.assertEqual(run.status, RunStatus.RUNNING)
        self.assertEqual(run.stage, "generating")
        self.assertIsNone(run.guide)
        self.assertIsNone(run.error)

    async def test_retry_claim_rejects_active_or_incomplete_run(self):
        active = self.retryable_run(RunStatus.RUNNING)
        with self.assertRaises(ConflictError):
            await self.service(FakeRunRepository(active)).retry_guide(active.id)

        incomplete = self.retryable_run()
        incomplete.study_result = None
        with self.assertRaises(ConflictError):
            await self.service(FakeRunRepository(incomplete)).retry_guide(incomplete.id)


def sample_film():
    return FilmProjection(
        id=uuid4(), slug="test-film", title="Test Film", original_title="Test Film",
        year=2000, countries=["US"], director="Director",
        releases=[FilmReleaseProjection(
            label="2000-01-01", year=2000, region="US", kind="premiere",
            source_url="https://example.com/film",
        )],
    )


def sample_evidence(film_id):
    return EvidenceRecord(
        id=uuid4(), film_id=film_id, source_url="https://example.com/source",
        source_title="Source", locator="section", body="Evidence", kind=EvidenceKind.FILM,
    )


if __name__ == "__main__":
    unittest.main()
