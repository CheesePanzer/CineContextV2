from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from unicodedata import combining, normalize
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ModelMode(StrEnum):
    OPENAI = "openai"
    MOCK = "mock"
    TACC = "tacc"


@dataclass(frozen=True)
class ModelReadiness:
    status: str
    job_id: str | None = None
    queued_seconds: int | None = None
    remaining_seconds: int | None = None
    last_confirmed_status: str | None = None
    last_confirmed_seconds: int | None = None


class ModelApiStyle(StrEnum):
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"


class ThinkingMode(StrEnum):
    DEFAULT = "default"
    ENABLED = "enabled"
    DISABLED = "disabled"


class ModelMessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"


class ModelResponseStatus(StrEnum):
    COMPLETED = "completed"


class StructuredOutputType(StrEnum):
    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"


class TimelineAxis(StrEnum):
    RELEASE = "release"
    STORY = "story"
    MYTH = "myth"
    HISTORY = "history"


class ResearchTimelineAxis(StrEnum):
    STORY = "story"
    MYTH = "myth"
    HISTORY = "history"


RESEARCH_TIMELINE_AXES = tuple(ResearchTimelineAxis)


class DecisionStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_EVIDENCE = "needs_evidence"


class Relevance(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class ProgressStage(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    SEARCHING = "searching"
    ASSESSING = "assessing"
    FOLLOW_UP = "follow_up"
    GENERATING = "generating"
    VALIDATING = "validating"
    FINISHED = "finished"


class OperationName(StrEnum):
    MODEL_PLAN = "model.plan"
    FIND_CANDIDATES = "find_candidates"
    DISCOVER_CATALOG = "discover_catalog"
    DISCOVER_EDITORIAL = "discover_editorial"
    SEARCH_EDITORIAL = "search_editorial"
    LOOKUP_LOCAL_MOVIE = "lookup_local_movie"
    LOOKUP_MOVIE = "lookup_movie"
    SEARCH_PASSAGES = "search_passages"
    SEARCH_PASSAGES_CONTEXT = "search_passages.context"
    SEARCH_PASSAGES_FOLLOW_UP = "search_passages.follow_up"
    SEARCH_WEB = "search_web"
    SEARCH_WEB_CONTEXT = "search_web.context"
    SEARCH_WEB_FOLLOW_UP = "search_web.follow_up"
    MODEL_ASSESS = "model.assess"
    MODEL_TIMELINE = "model.timeline"
    MODEL_GENERATE = "model.generate"
    MODEL_REVISE = "model.revise"
    LOOKUP_RATINGS = "lookup_ratings"


class ToolCallStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    FAILED = "failed"


class EvidenceKind(StrEnum):
    METADATA = "metadata"
    FILM = "film"
    EDITORIAL = "editorial"
    CONTEXT = "context"
    WEB_EXCERPT = "web_excerpt"
    SEARCH_SUMMARY = "search_summary"


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str = ""


@dataclass(frozen=True)
class PageContent:
    url: str
    title: str
    text: str


class FilmReleaseProjection(BaseModel):
    label: str
    year: int
    region: str
    kind: str
    source_url: str


def film_title_key(value: str) -> str:
    return "".join(character for character in normalize("NFKD", value.strip().casefold())
                   if not combining(character))


class FilmProjection(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: UUID
    slug: str
    title: str
    original_title: str
    tmdb_id: int | None = None
    imdb_id: str | None = None
    poster_path: str | None = None
    year: int
    countries: list[str] = Field(default_factory=list)
    director: str = ""
    story: str = ""
    reason: str = ""
    releases: list[FilmReleaseProjection] = Field(min_length=1)


class EvidenceDraft(BaseModel):
    passage_id: UUID | None = None
    film_id: UUID | None = None
    source_title: str
    source_url: str
    locator: str
    body: str
    kind: EvidenceKind


class EvidenceRecord(BaseModel):
    id: UUID
    film_id: UUID | None = None
    source_url: str
    source_title: str
    locator: str
    body: str
    kind: EvidenceKind


class ValidatedDecision(BaseModel):
    film_id: UUID
    status: DecisionStatus
    reason: str
    relevance: Relevance = Relevance.PRIMARY
    preference_fit: int = Field(default=0, ge=0, le=2)
    evidence_ids: list[UUID] = Field(default_factory=list)
    next_query: str = ""


@dataclass(frozen=True)
class FollowUpKey:
    film_id: UUID
    query: str


@dataclass(frozen=True)
class FollowUpRequest:
    film: FilmProjection
    query: str

    @property
    def key(self) -> FollowUpKey:
        return FollowUpKey(self.film.id, self.query)


class StudyDecision(ValidatedDecision):
    title: str
    year: int


class TimelineViewEvent(BaseModel):
    id: str
    label: str
    period: str
    sort_year: int | None = None
    film_ids: list[UUID]
    evidence_ids: list[UUID]
    relation: str
    axis: ResearchTimelineAxis | None = None
    version: str | None = None
    order_group: int | None = None

    def to_json(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        for name in ("axis", "version", "order_group"):
            if payload[name] is None:
                del payload[name]
        return payload


@dataclass(frozen=True)
class ValidatedTimelineEdge:
    earlier: str
    later: str
    evidence_ids: list[UUID]


class ValidatedTimeline(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    views: dict[TimelineAxis, list[TimelineViewEvent]] = Field(default_factory=dict)
    valid_edges: list[ValidatedTimelineEdge] = Field(default_factory=list)

    def views_json(self) -> dict[str, list[dict[str, Any]]]:
        return {
            axis.value: [event.to_json() for event in events]
            for axis, events in self.views.items()
        }


class StudyPayload(BaseModel):
    films: list[FilmProjection]
    decisions: list[StudyDecision]
    evidence: list[EvidenceRecord]
    limitations: list[str]
    follow_ups: int
    timeline: ValidatedTimeline | None = Field(default=None, exclude=True)

    def to_json(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude_none=True)
        if self.timeline is not None:
            payload["timeline_views"] = self.timeline.views_json()
        return payload
