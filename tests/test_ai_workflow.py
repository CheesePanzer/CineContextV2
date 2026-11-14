import unittest
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
from alcedo import ConflictError, NotFoundError, Options
from pydantic import BaseModel
from starlette.testclient import TestClient
from cinecontext.ai.curation import CurationService
from cinecontext.ai.openai_model import ModelOutputValidationError, ModelResponse, OpenAIModel
from cinecontext.ai.nodes import WorkflowOperations
from cinecontext.ai.nodes import AssessNode
from cinecontext.application.services import RunApplicationService
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
)
from cinecontext.ai.research_tools import create_research_tools
from cinecontext.ai.research_tools.crawler import VisibleHTMLParser, validate_public_url
from cinecontext.ai.research_tools.duckduckgo import DuckDuckGoHTMLParser
from cinecontext.ai.guide_validation import GuideValidator
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
from cinecontext.contracts import Claim, Constraints, FilmGuide, Guide, TimelineResearch
from cinecontext.domain import CurationRun, RunStatus
from cinecontext.observability import configure_logging, sanitize_message
from cinecontext.settings import LoggingOptions, ModelOptions, ToolsOptions
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
        evidence = sample_evidence(film.id)
        research = TimelineResearch.model_validate({
            "events": [{
                "id": "story-event", "axis": ResearchTimelineAxis.STORY,
                "label": "Setting", "period": "1990", "sort_year": 1990,
                "film_ids": [film.id], "relation": "Setting", "evidence_ids": [evidence.id],
            }],
            "order": [],
        })
        views = TimelineBuilder().build([film], research, {evidence.id}).views_json()
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

    def test_timeline_filters_events_with_unpublished_evidence(self):
        film = sample_film()
        research = TimelineResearch.model_validate({
            "events": [{
                "id": "unsupported", "axis": ResearchTimelineAxis.STORY,
                "label": "Setting", "period": "1990", "sort_year": 1990,
                "film_ids": [film.id], "relation": "Setting", "evidence_ids": [uuid4()],
            }],
        })

        timeline = TimelineBuilder().build([film], research, set())

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

    async def test_invalid_json_is_retried_then_succeeds(self):
        model = OpenAIModel(ModelOptions(
            api_key="test", model_id="test", structured_output_retries=1,
        ), LoggingOptions(capture_model_responses=False))
        responses = iter([
            ('{"value":"truncated"', "length"),
            ('{"value":"complete"}', "stop"),
        ])

        async def complete(system, payload):
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

        async def complete(system, payload):
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

            async def complete(system, payload):
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
