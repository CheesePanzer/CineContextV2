from collections.abc import Sequence
from typing import Any, Protocol
from uuid import UUID

from ..ai.types import EvidenceDraft, FilmProjection, ProgressStage
from ..contracts import FurtherReading
from ..domain import RunStatus


class TaccJobControl(Protocol):
    @property
    def job_id(self) -> str | None: ...
    async def submit_after(self, previous_job_id: str, *, restart: bool = False) -> str: ...


class ReferenceSearch(Protocol):
    async def search(self, queries: list[str]) -> list[EvidenceDraft]: ...


class FilmReadings(Protocol):
    async def for_film(self, film: FilmProjection) -> list[FurtherReading]: ...


class CurationPersistence(Protocol):
    async def advance_run(self, run_id: UUID, stage: ProgressStage, message: str) -> None: ...
    async def finish_run(
        self, run_id: UUID, status: RunStatus, message: str, guide: dict[str, Any] | None = None
    ) -> None: ...
    async def record_tool_call(
        self, run_id: UUID, operation: str, status: str, elapsed_ms: int, detail: dict[str, Any]
    ) -> None: ...
    async def add_evidence(self, run_id: UUID, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]: ...
    async def save_study(
        self,
        run_id: UUID,
        films: Sequence[dict[str, Any]],
        decisions: dict[str, dict[str, Any]],
        study: dict[str, Any],
    ) -> None: ...
    async def save_timeline(
        self,
        run_id: UUID,
        study: dict[str, Any],
        views: dict[str, list[dict[str, Any]]],
        valid_edges: Sequence[Any],
        evidence: Sequence[dict[str, Any]],
    ) -> None: ...
    async def find_candidates(self, theme: str) -> Sequence[UUID]: ...
    async def get_film_projection(self, film_id: UUID) -> dict[str, Any]: ...
    async def save_film_poster(self, film_id: UUID, poster_path: str) -> None: ...
    async def save_tmdb_film(
        self,
        tmdb_id: int,
        imdb_id: str | None,
        title: str,
        original_title: str,
        facts: dict[str, Any],
        release: str,
        source_url: str,
        kind: str = "tmdb_release",
    ) -> UUID: ...
    async def search_passages(
        self, query: str, film_id: UUID | None = None, limit: int = 5
    ) -> list[dict[str, Any]]: ...
    async def save_ratings(
        self, film_id: UUID, ratings: Sequence[dict[str, Any]]
    ) -> None: ...
