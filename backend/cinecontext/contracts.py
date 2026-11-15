from typing import Annotated, Literal
from uuid import UUID

from alcedo import CommandBase
from pydantic import BaseModel, Field, HttpUrl, StringConstraints, field_validator, model_validator

from .ai.types import DecisionStatus, Relevance, ResearchTimelineAxis, TimelineAxis


Preference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class Constraints(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    theme: str = Field(max_length=100)
    research_title: str = Field(default="", max_length=72)
    count: int = Field(default=3, ge=1, le=5)
    region: str = Field(default="", max_length=80)
    release_from: int | None = Field(default=None, ge=1880, le=2200)
    release_to: int | None = Field(default=None, ge=1880, le=2200)
    story_period: str = Field(default="", max_length=120)
    movement: str = Field(default="", max_length=120)
    preferred_genres: list[Preference] = Field(default_factory=list, max_length=5)
    preferred_styles: list[Preference] = Field(default_factory=list, max_length=5)
    avoid: str = Field(default="", max_length=180)
    setting_match: Literal["strict", "nearby"] = "strict"
    axis: TimelineAxis = TimelineAxis.RELEASE

    @field_validator("research_title", mode="before")
    @classmethod
    def normalize_research_title(cls, value):
        if not isinstance(value, str):
            return ""
        title = " ".join(value.split()).rstrip(".… ")
        return title if len(title) <= 72 else ""

    @model_validator(mode="after")
    def check_range(self):
        if self.release_from and self.release_to and self.release_from > self.release_to:
            raise ValueError("The start year must be no later than the end year")
        return self


class ParseRequest(CommandBase):
    prompt: str = Field(min_length=1, max_length=500, pattern=r"\S")


class ReferenceMatch(BaseModel):
    title: str = Field(min_length=1, max_length=150)
    story_setting: str = Field(min_length=1, max_length=120)
    source_title: str
    source_url: HttpUrl
    context: str = Field(default="", max_length=360)
    suggested_themes: list[Preference] = Field(default_factory=list, max_length=4)


class ParsedRequest(BaseModel):
    constraints: Constraints
    clarification: str = ""
    reference_queries: list[str] = Field(default_factory=list, max_length=3, exclude=True)
    reference_match: ReferenceMatch | None = None
    preliminary_analysis: str = Field(default="", max_length=450)
    suggested_themes: list[Preference] = Field(default_factory=list, max_length=4)
    suggested_styles: list[Preference] = Field(default_factory=list, max_length=4)


class TaccSubmissionRequest(CommandBase):
    previous_job_id: str = Field(pattern=r"^[0-9]+$")
    restart: bool = False


class RunRequest(CommandBase):
    submission_key: UUID
    constraints: Constraints


class FurtherReading(BaseModel):
    publication: str
    title: str
    url: HttpUrl
    summary: str | None = None
    author: str | None = None


class FilmHint(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    year: int | None = Field(default=None, ge=1880, le=2200)


class Plan(BaseModel):
    query: str = Field(min_length=1, max_length=250)
    hints: list[FilmHint] = Field(default_factory=list, max_length=10)
    discovery_terms: list[Preference] = Field(default_factory=list, max_length=4)


class FilmAssessment(BaseModel):
    film_id: UUID
    status: DecisionStatus
    reason: str
    relevance: Relevance = Relevance.PRIMARY
    preference_fit: int = Field(default=0, ge=0, le=2)
    evidence_ids: list[UUID] = Field(default_factory=list)
    next_query: str = Field(default="", max_length=250)
    setting_quote: str = Field(default="", max_length=500)


class Assessment(BaseModel):
    decisions: list[FilmAssessment] = Field(max_length=10)


class Claim(BaseModel):
    title: str
    text: str
    evidence_ids: list[UUID] = Field(min_length=1, max_length=8)


class FilmGuide(BaseModel):
    film_id: UUID
    reason: Claim
    primer: list[Claim] = Field(default_factory=list, max_length=3)
    background: list[Claim] = Field(default_factory=list, max_length=4)


class Guide(BaseModel):
    title: str
    introduction: str
    common_primer: list[Claim] = Field(default_factory=list, max_length=5)
    film_guides: list[FilmGuide] = Field(max_length=5)
    viewing_order: list[UUID] = Field(max_length=5)
    discussion_questions: list[str] = Field(default_factory=list, max_length=5)


class TimelineEvent(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    axis: ResearchTimelineAxis
    label: str
    period: str
    sort_year: int | None = None
    version: str = ""
    film_ids: list[UUID] = Field(default_factory=list, max_length=5)
    relation: str = ""
    evidence_ids: list[UUID] = Field(min_length=1, max_length=8)


class EventOrder(BaseModel):
    earlier: str
    later: str
    evidence_ids: list[UUID] = Field(min_length=1, max_length=8)


class TimelineResearch(BaseModel):
    events: list[TimelineEvent] = Field(default_factory=list, max_length=18)
    order: list[EventOrder] = Field(default_factory=list, max_length=24)


class Rating(BaseModel):
    origin: str
    metric: str
    value: float = Field(ge=0)
    scale: Literal[10, 100]
    count: int | None = Field(default=None, ge=0)
    source_url: HttpUrl

    @model_validator(mode="after")
    def check_scale(self):
        if self.value > self.scale:
            raise ValueError("score exceeds scale")
        return self
