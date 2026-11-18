from typing import Annotated

from pydantic import BaseModel, Field, OnErrorOmit

from ..contracts import Assessment, FilmAssessment
from .types import DecisionStatus, EvidenceRecord, FilmProjection, Relevance


SourceNumber = Annotated[int, Field(strict=True, ge=1)]


class DraftFilmAssessment(BaseModel):
    film: int = Field(strict=True, ge=1)
    status: DecisionStatus
    reason: str
    relevance: Relevance = Relevance.PRIMARY
    preference_fit: int = Field(default=0, ge=0, le=2)
    sources: list[OnErrorOmit[SourceNumber]] = Field(default_factory=list)
    next_query: str = Field(default="", max_length=250)
    setting_quote: str = Field(default="", max_length=500)


class AssessmentDraft(BaseModel):
    decisions: list[DraftFilmAssessment] = Field(max_length=10)

    def resolve(self, films: list[FilmProjection], evidence: list[EvidenceRecord]) -> Assessment:
        films_by_number = dict(enumerate(films, start=1))
        sources_by_number = dict(enumerate(evidence, start=1))
        decisions = []
        for draft in self.decisions:
            film = films_by_number.get(draft.film)
            if film is None:
                continue
            evidence_ids = []
            for number in dict.fromkeys(draft.sources):
                source = sources_by_number.get(number)
                if source is not None and source.film_id in {None, film.id}:
                    evidence_ids.append(source.id)
            decisions.append(FilmAssessment(
                film_id=film.id, evidence_ids=evidence_ids,
                **draft.model_dump(exclude={"film", "sources"}),
            ))
        return Assessment(decisions=decisions)
