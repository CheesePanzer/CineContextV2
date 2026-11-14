from ..contracts import Guide
from .types import EvidenceRecord, FilmProjection


class GuideValidator:
    """Validate film coverage and citation ownership without retaining run state."""

    def validate(
        self, guide: Guide, films: list[FilmProjection], evidence: list[EvidenceRecord]
    ) -> None:
        expected = {film.id for film in films}
        ids = [item.film_id for item in guide.film_guides]
        order = guide.viewing_order
        if len(ids) != len(expected) or set(ids) != expected or len(order) != len(expected) or set(order) != expected:
            raise ValueError("film_guides and viewing_order must each include every selected film exactly once")
        by_id = {item.id: item for item in evidence}
        groups = [(None, guide.common_primer)]
        groups += [(item.film_id, [item.reason, *item.primer, *item.background]) for item in guide.film_guides]
        for film_id, claims in groups:
            for claim in claims:
                for ref in claim.evidence_ids:
                    source = by_id.get(ref)
                    if not source or (film_id is not None and source.film_id not in {film_id, None}):
                        raise ValueError(f"Citation {ref} does not belong to this run or film")
