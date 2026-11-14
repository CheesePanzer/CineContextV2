from uuid import UUID

from ...application.ports import CurationPersistence
from ..types import EvidenceDraft, FilmProjection


class LocalResearchProvider:
    def __init__(self, persistence: CurationPersistence):
        self.persistence = persistence

    async def find_candidates(self, theme: str):
        return await self.persistence.find_candidates(theme)

    async def lookup_movie(self, film_id: UUID) -> FilmProjection:
        row = await self.persistence.get_film_projection(film_id)
        return FilmProjection.model_validate(row)

    async def search_passages(
        self, query: str, film_id: UUID | None = None
    ) -> list[EvidenceDraft]:
        rows = await self.persistence.search_passages(query, film_id)
        return [EvidenceDraft.model_validate(row) for row in rows]
