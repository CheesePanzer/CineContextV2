from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

import httpx

from ...application.ports import CurationPersistence
from ...contracts import FilmHint, Rating
from ...settings import ToolsOptions
from ..types import EvidenceDraft, FilmProjection
from .brave import BraveSearchProvider
from .crawler import WebCrawler, resolve_host
from .duckduckgo import DuckDuckGoSearchProvider
from .local import LocalResearchProvider
from .omdb import OmdbRatingsProvider
from .ratings import RatingStore
from .tmdb import TmdbMetadataProvider


class FilmMetadataProvider(Protocol):
    async def lookup_movie(self, hint: FilmHint) -> FilmProjection | None: ...


class WebSearchProvider(Protocol):
    async def search(
        self, query: str, film_id: UUID | None = None
    ) -> list[EvidenceDraft]: ...


class RatingsProvider(Protocol):
    async def lookup(self, film: FilmProjection) -> list[Rating]: ...


class ResearchTools(Protocol):
    @property
    def metadata_enabled(self) -> bool: ...

    @property
    def web_enabled(self) -> bool: ...

    @property
    def ratings_enabled(self) -> bool: ...

    async def find_candidates(self, theme: str) -> Sequence[UUID]: ...
    async def lookup_local_movie(self, film_id: UUID) -> FilmProjection: ...
    async def lookup_movie(self, hint: FilmHint) -> FilmProjection | None: ...
    async def search_passages(
        self, query: str, film_id: UUID | None = None
    ) -> list[EvidenceDraft]: ...
    async def search_web(
        self, query: str, film_id: UUID | None = None
    ) -> list[EvidenceDraft]: ...
    async def lookup_ratings(self, film: FilmProjection) -> list[Rating]: ...


class ResearchToolsFacade:
    def __init__(
        self,
        local: LocalResearchProvider,
        metadata: FilmMetadataProvider,
        web: WebSearchProvider,
        ratings: RatingsProvider,
        options: ToolsOptions,
    ):
        self.local = local
        self.metadata = metadata
        self.web = web
        self.ratings = ratings
        self.options = options

    @property
    def metadata_enabled(self) -> bool:
        return self.options.tmdb.enabled

    @property
    def web_enabled(self) -> bool:
        return self.options.brave.enabled or self.options.duckduckgo.enabled

    @property
    def ratings_enabled(self) -> bool:
        return self.options.omdb.enabled

    async def find_candidates(self, theme: str):
        return await self.local.find_candidates(theme)

    async def lookup_local_movie(self, film_id: UUID):
        return await self.local.lookup_movie(film_id)

    async def lookup_movie(self, hint: FilmHint):
        return await self.metadata.lookup_movie(hint)

    async def search_passages(self, query: str, film_id: UUID | None = None):
        return await self.local.search_passages(query, film_id)

    async def search_web(self, query: str, film_id: UUID | None = None):
        return await self.web.search(query, film_id)

    async def lookup_ratings(self, film: FilmProjection):
        return await self.ratings.lookup(film)


def create_research_tools(
    persistence: CurationPersistence,
    http: httpx.AsyncClient,
    options: ToolsOptions,
    resolver=resolve_host,
) -> ResearchTools:
    local = LocalResearchProvider(persistence)
    rating_store = RatingStore(persistence)
    metadata = TmdbMetadataProvider(persistence, http, options.tmdb, local, rating_store)
    ratings = OmdbRatingsProvider(http, options.omdb, rating_store)
    if options.brave.enabled:
        web = BraveSearchProvider(http, options.brave)
    else:
        crawler = WebCrawler(http, options.crawler, resolver)
        web = DuckDuckGoSearchProvider(options.duckduckgo, options.crawler, crawler)
    return ResearchToolsFacade(local, metadata, web, ratings, options)
