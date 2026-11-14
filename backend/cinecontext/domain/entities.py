from enum import StrEnum
from uuid import UUID

from alcedo import AggregateRoot, Entity, StateFlow, StateTransition
from sqlalchemy import Boolean, Computed, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column


class Film(AggregateRoot):
    __tablename__ = "films"

    slug: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(String)
    original_title: Mapped[str] = mapped_column(String)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    imdb_id: Mapped[str | None] = mapped_column(String, unique=True)
    facts: Mapped[dict] = mapped_column(JSONB)


class FilmRelease(Entity):
    __tablename__ = "film_releases"
    __table_args__ = (UniqueConstraint("film_id", "label", "region", "kind"),)

    film_id: Mapped[UUID] = mapped_column(ForeignKey("films.id"))
    label: Mapped[str] = mapped_column(String)
    year: Mapped[int] = mapped_column(Integer)
    region: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    source_url: Mapped[str] = mapped_column(Text)


class Source(AggregateRoot):
    __tablename__ = "sources"

    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    usage_note: Mapped[str] = mapped_column(Text)


class Passage(Entity):
    __tablename__ = "passages"
    __table_args__ = (
        UniqueConstraint("source_id", "locator", "revision"),
        Index("ix_passages_search", "search_vector", postgresql_using="gin"),
        Index("ix_passages_current", "source_id", "locator", unique=True,
              postgresql_where=text("is_current")),
    )

    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"))
    locator: Mapped[str] = mapped_column(String)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    body: Mapped[str] = mapped_column(Text)
    search_terms: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple'::regconfig, body || ' ' || search_terms)", persisted=True)
    )


class FilmPassage(Entity):
    __tablename__ = "film_passages"
    __table_args__ = (UniqueConstraint("film_id", "passage_id"),)

    film_id: Mapped[UUID] = mapped_column(ForeignKey("films.id"), index=True)
    passage_id: Mapped[UUID] = mapped_column(ForeignKey("passages.id"))


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class RunTrigger(StrEnum):
    START = "start"
    COMPLETE = "complete"
    COMPLETE_PARTIAL = "complete_partial"
    FAIL = "fail"
    INTERRUPT = "interrupt"
    RETRY_GUIDE = "retry_guide"


RUN_FLOW = StateFlow([
    StateTransition(RunStatus.QUEUED, RunTrigger.START, RunStatus.RUNNING),
    StateTransition(RunStatus.QUEUED, RunTrigger.INTERRUPT, RunStatus.INTERRUPTED),
    StateTransition(RunStatus.RUNNING, RunTrigger.COMPLETE, RunStatus.COMPLETED),
    StateTransition(RunStatus.RUNNING, RunTrigger.COMPLETE_PARTIAL, RunStatus.PARTIAL),
    StateTransition(RunStatus.RUNNING, RunTrigger.FAIL, RunStatus.FAILED),
    StateTransition(RunStatus.RUNNING, RunTrigger.INTERRUPT, RunStatus.INTERRUPTED),
    StateTransition(RunStatus.PARTIAL, RunTrigger.RETRY_GUIDE, RunStatus.RUNNING),
    StateTransition(RunStatus.FAILED, RunTrigger.RETRY_GUIDE, RunStatus.RUNNING),
    StateTransition(RunStatus.INTERRUPTED, RunTrigger.RETRY_GUIDE, RunStatus.RUNNING),
])


class CurationRun(AggregateRoot):
    __tablename__ = "runs"

    submission_key: Mapped[UUID] = mapped_column(unique=True)
    constraints: Mapped[dict] = mapped_column(JSONB)
    model_mode: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    stage: Mapped[str] = mapped_column(String)
    progress: Mapped[list] = mapped_column(JSONB)
    study_result: Mapped[dict | None] = mapped_column(JSONB)
    guide: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    @classmethod
    def start(cls, submission_key: UUID, constraints: dict, mode: str):
        return cls(
            submission_key=submission_key, constraints=constraints, model_mode=mode,
            status=RunStatus.QUEUED, stage="queued", progress=[],
        )

    def advance(self, stage: str, message: str):
        status = RunStatus(self.status)
        if status == RunStatus.QUEUED:
            status = RUN_FLOW.transition(status, RunTrigger.START)
        elif status != RunStatus.RUNNING:
            raise ValueError("Task has already ended")
        self.status = status
        self.stage = stage
        self.progress = [*self.progress, {"stage": stage, "message": message}]

    def finish(self, status: str, message: str, guide: dict | None = None):
        current = RunStatus(self.status)
        if current == RunStatus.QUEUED:
            current = RUN_FLOW.transition(current, RunTrigger.START)
        trigger = {
            RunStatus.COMPLETED: RunTrigger.COMPLETE,
            RunStatus.PARTIAL: RunTrigger.COMPLETE_PARTIAL,
            RunStatus.FAILED: RunTrigger.FAIL,
            RunStatus.INTERRUPTED: RunTrigger.INTERRUPT,
        }[RunStatus(status)]
        self.status = RUN_FLOW.transition(current, trigger)
        self.stage = "finished"
        self.progress = [*self.progress, {"stage": "finished", "message": message}]
        self.guide = guide
        if self.status in {RunStatus.FAILED, RunStatus.INTERRUPTED}:
            self.error = message
        else:
            self.error = None

    def begin_guide_retry(self):
        self.status = RUN_FLOW.transition(
            RunStatus(self.status), RunTrigger.RETRY_GUIDE,
        )
        self.stage = "generating"
        self.guide = None
        self.error = None
        self.progress = [
            *self.progress,
            {"stage": "generating", "message": "Retrying viewing guide generation."},
        ]


class CandidateDecision(Entity):
    __tablename__ = "run_films"
    __table_args__ = (UniqueConstraint("run_id", "film_id"),)

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    film_id: Mapped[UUID] = mapped_column(ForeignKey("films.id"))
    decision: Mapped[str] = mapped_column(String)
    explanation: Mapped[dict] = mapped_column(JSONB)


class EvidenceSnapshot(Entity):
    __tablename__ = "run_evidence"
    __table_args__ = (UniqueConstraint("run_id", "id"),)

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    film_id: Mapped[UUID | None] = mapped_column(ForeignKey("films.id"))
    passage_id: Mapped[UUID | None] = mapped_column(ForeignKey("passages.id"))
    source_title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    locator: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String)


class ToolCall(Entity):
    __tablename__ = "tool_calls"

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    operation: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    elapsed_ms: Mapped[int] = mapped_column(Integer)
    detail: Mapped[dict] = mapped_column(JSONB)


class StoredTimelineEvent(Entity):
    __tablename__ = "timeline_events"
    __table_args__ = (
        UniqueConstraint("run_id", "id"),
        UniqueConstraint("run_id", "event_key"),
        ForeignKeyConstraint(["run_id", "evidence_id"], ["run_evidence.run_id", "run_evidence.id"]),
    )

    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    event_key: Mapped[str] = mapped_column(String)
    axis: Mapped[str] = mapped_column(String)
    evidence_id: Mapped[UUID]
    data: Mapped[dict] = mapped_column(JSONB)


class TimelineEdge(Entity):
    __tablename__ = "timeline_edges"
    __table_args__ = (
        UniqueConstraint("run_id", "earlier_id", "later_id"),
        ForeignKeyConstraint(["run_id", "earlier_id"], ["timeline_events.run_id", "timeline_events.id"]),
        ForeignKeyConstraint(["run_id", "later_id"], ["timeline_events.run_id", "timeline_events.id"]),
        ForeignKeyConstraint(["run_id", "evidence_id"], ["run_evidence.run_id", "run_evidence.id"]),
    )

    run_id: Mapped[UUID]
    earlier_id: Mapped[UUID]
    later_id: Mapped[UUID]
    evidence_id: Mapped[UUID]


class FilmEventLink(Entity):
    __tablename__ = "film_events"
    __table_args__ = (
        UniqueConstraint("run_id", "film_id", "event_id"),
        ForeignKeyConstraint(["run_id", "film_id"], ["run_films.run_id", "run_films.film_id"]),
        ForeignKeyConstraint(["run_id", "event_id"], ["timeline_events.run_id", "timeline_events.id"]),
        ForeignKeyConstraint(["run_id", "evidence_id"], ["run_evidence.run_id", "run_evidence.id"]),
    )

    run_id: Mapped[UUID]
    film_id: Mapped[UUID]
    event_id: Mapped[UUID]
    evidence_id: Mapped[UUID]
    relation: Mapped[str] = mapped_column(String)


class RatingSnapshot(Entity):
    __tablename__ = "rating_snapshots"
    __table_args__ = (UniqueConstraint("film_id", "origin", "metric"),)

    film_id: Mapped[UUID] = mapped_column(ForeignKey("films.id"))
    origin: Mapped[str] = mapped_column(String)
    metric: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB)
