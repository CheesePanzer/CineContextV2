from dataclasses import dataclass

from ..contracts import Constraints
from .types import EvidenceRecord, FilmProjection


@dataclass(frozen=True)
class GuideRetrySnapshot:
    constraints: Constraints
    films: list[FilmProjection]
    evidence: list[EvidenceRecord]

    def published_evidence(self) -> list[EvidenceRecord]:
        film_ids = {film.id for film in self.films}
        return [
            item for item in self.evidence
            if item.film_id is None or item.film_id in film_ids
        ]
