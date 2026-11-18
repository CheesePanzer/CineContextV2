from __future__ import annotations

import asyncio
from collections.abc import Sequence
import logging
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

import httpx

from ...contracts import Constraints, FilmHint, Rating
from ...settings import ToolsOptions
from ..types import EvidenceDraft, FilmProjection
from .brave import BraveSearchProvider
from .crawler import WebCrawler, resolve_host
from .duckduckgo import DuckDuckGoSearchProvider
from .editorial import curated_candidates, curated_evidence
from .local import LocalResearchProvider
from .omdb import OmdbRatingsProvider
from .ratings import RatingStore
from .tmdb import TmdbMetadataProvider
from .wikipedia import WikipediaSearchProvider

if TYPE_CHECKING:
    from ...application.ports import CurationPersistence

logger = logging.getLogger(__name__)


class FilmMetadataProvider(Protocol):
    async def discover(self, terms: list[str], constraints: Constraints) -> list[FilmHint]: ...
    async def lookup_movie(self, hint: FilmHint) -> FilmProjection | None: ...
    async def enrich_local_movie(self, film: FilmProjection) -> None: ...


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
    async def discover_catalog(self, terms: list[str], constraints: Constraints) -> list[FilmHint]: ...
    async def discover_editorial(self, constraints: Constraints, terms: list[str]) -> list[FilmHint]: ...
    async def search_editorial(self, film: FilmProjection) -> list[EvidenceDraft]: ...
    async def lookup_local_movie(self, film_id: UUID) -> FilmProjection: ...
    async def lookup_movie(self, hint: FilmHint) -> FilmProjection | None: ...
    async def enrich_local_movie(self, film: FilmProjection) -> None: ...
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
        return self.options.brave.enabled or self.options.wikipedia_enabled or self.options.duckduckgo.enabled

    @property
    def ratings_enabled(self) -> bool:
        return self.options.omdb.enabled

    async def find_candidates(self, theme: str):
        return await self.local.find_candidates(theme)

    async def lookup_local_movie(self, film_id: UUID):
        return await self.local.lookup_movie(film_id)

    async def discover_catalog(self, terms: list[str], constraints: Constraints) -> list[FilmHint]:
        return await self.metadata.discover(terms, constraints)

    async def discover_editorial(self, constraints: Constraints, terms: list[str]) -> list[FilmHint]:
        return curated_candidates(constraints, terms)

    async def search_editorial(self, film: FilmProjection) -> list[EvidenceDraft]:
        return curated_evidence(film)

    async def lookup_movie(self, hint: FilmHint):
        return await self.metadata.lookup_movie(hint)

    async def enrich_local_movie(self, film: FilmProjection) -> None:
        await self.metadata.enrich_local_movie(film)

    async def search_passages(self, query: str, film_id: UUID | None = None):
        return await self.local.search_passages(query, film_id)

    async def search_web(self, query: str, film_id: UUID | None = None):
        try:
            return await self.web.search(query, film_id)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ProxyError, httpx.RemoteProtocolError):
            pass
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in {502, 503, 504}:
                raise
        logger.debug("Retrying web research after a temporary upstream failure")
        await asyncio.sleep(0.5)
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
        web_name = "brave"
    elif options.wikipedia_enabled:
        web = WikipediaSearchProvider(http, local)
        web_name = "wikipedia"
    else:
        crawler = WebCrawler(http, options.crawler, resolver)
        web = DuckDuckGoSearchProvider(options.duckduckgo, options.crawler, crawler)
        web_name = "duckduckgo"
    logger.debug(
        "Research providers selected web=%s metadata=%s ratings=%s crawler=%s",
        web_name, "tmdb" if options.tmdb.enabled else "local",
        "omdb" if options.omdb.enabled else "disabled", options.crawler.enabled,
    )
    return ResearchToolsFacade(local, metadata, web, ratings, options)
