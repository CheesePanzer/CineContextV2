from typing import Annotated

from pydantic import BaseModel, Field, OnErrorOmit, StringConstraints

from ..contracts import Claim, FilmGuide, Guide
from .types import EvidenceKind, EvidenceRecord, FilmProjection


NonemptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DraftClaim(BaseModel):
    title: NonemptyText
    text: NonemptyText
    sources: list[int] = Field(min_length=1, max_length=2)


class DraftFilmGuide(BaseModel):
    film: int
    reason: DraftClaim
    primer: list[OnErrorOmit[DraftClaim]] = Field(default_factory=list, max_length=3)
    background: list[OnErrorOmit[DraftClaim]] = Field(default_factory=list, max_length=4)


class GuideDraft(BaseModel):
    title: NonemptyText
    introduction: NonemptyText
    common_primer: list[OnErrorOmit[DraftClaim]] = Field(default_factory=list, max_length=5)
    film_guides: list[DraftFilmGuide] = Field(max_length=5)
    viewing_order: list[int] = Field(max_length=5)
    discussion_questions: list[OnErrorOmit[str]] = Field(default_factory=list, max_length=5)

    def resolve(self, films: list[FilmProjection], evidence: list[EvidenceRecord]) -> Guide:
        films_by_number = dict(enumerate(films, start=1))
        sources_by_number = dict(enumerate(evidence, start=1))

        def film_id(number):
            if number not in films_by_number:
                raise ValueError(f"Film number {number} is outside the selected films")
            return films_by_number[number].id

        def claim(draft, owner=None):
            sources = [sources_by_number.get(number) for number in draft.sources]
            if any(source is None or (owner is not None and source.film_id not in {None, owner}) for source in sources):
                return None
            return Claim(title=draft.title, text=draft.text, evidence_ids=[source.id for source in sources])

        def notes(drafts, owner=None):
            return [note for draft in drafts if (note := claim(draft, owner)) is not None]

        film_guides = []
        for item in self.film_guides:
            owner = film_id(item.film)
            reason = claim(item.reason, owner)
            if reason is None:
                raise ValueError(f"Film {item.film}: use sources listed for this film or general context")
            if not any(sources_by_number[number].film_id in {None, owner}
                       and sources_by_number[number].kind != EvidenceKind.METADATA
                       for number in item.reason.sources):
                raise ValueError(f"Film {item.film}: cite a non-metadata source connecting the film to the topic")
            film_guides.append(FilmGuide(
                film_id=owner, reason=reason,
                primer=notes(item.primer, owner), background=notes(item.background, owner),
            ))
        return Guide(
            title=self.title, introduction=self.introduction,
            common_primer=notes(self.common_primer),
            film_guides=film_guides, viewing_order=[film_id(number) for number in self.viewing_order],
            discussion_questions=self.discussion_questions,
        )
