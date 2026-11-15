from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

import httpx

from ...contracts import Constraints, FilmHint, Rating
from ...settings import TmdbOptions
from ..types import FilmProjection, film_title_key
from .local import LocalResearchProvider
from .ratings import RatingStore

if TYPE_CHECKING:
    from ...application.ports import CurationPersistence


def tmdb_poster_path(value: object) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"/[A-Za-z0-9_-]+\.(?:jpg|png|webp)", value):
        return value
    return None


class TmdbMetadataProvider:
    def __init__(
        self,
        persistence: CurationPersistence,
        http: httpx.AsyncClient,
        options: TmdbOptions,
        local: LocalResearchProvider,
        ratings: RatingStore,
    ):
        self.persistence = persistence
        self.http = http
        self.options = options
        self.local = local
        self.ratings = ratings

    async def _get(self, path: str, **params):
        url = f"{self.options.api_base_url.rstrip('/')}/{path}"
        headers = {"Authorization": f"Bearer {self.options.access_token}"}
        try:
            response = await self.http.get(url, params=params, headers=headers)
        except httpx.TransportError:
            await asyncio.sleep(0.5)
            response = await self.http.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    async def discover(self, terms: list[str], constraints: Constraints) -> list[FilmHint]:
        if not self.options.enabled:
            return []

        async def find_keyword(term):
            data = await self._get("search/keyword", query=term)
            rows = data.get("results", [])
            exact = [row["id"] for row in rows if row.get("name", "").casefold() == term]
            # TMDB can index singular/plural variants or date-qualified tags independently.
            variant = term[:-1] if term.endswith("s") else f"{term}s"
            related = [row["id"] for row in rows if row.get("name", "").casefold() == variant
                       or row.get("name", "").casefold().startswith(f"{term} (")]
            return list(dict.fromkeys(exact + related))[:2]

        style_terms = {value.casefold() for value in constraints.preferred_styles}
        terms = list(dict.fromkeys(
            term.strip().casefold() for term in terms
            if term.strip() and term.strip().casefold() not in style_terms
        ))[:4]
        # A catalog tag that names the main theme keeps broad mood and style tags
        # from introducing unrelated films into the limited assessment pool.
        theme = re.split(r"[,;]", constraints.theme, maxsplit=1)[0].strip().casefold()
        if theme and len(theme) <= 50:
            terms = [theme, *(term for term in terms if term != theme)][:5]
        if not terms:
            return []
        keyword_matches = await asyncio.gather(*(find_keyword(term) for term in terms), return_exceptions=True)
        if theme and terms[0] == theme and isinstance(keyword_matches[0], list) and keyword_matches[0]:
            keyword_matches = keyword_matches[:1]
        filters = {"include_adult": "false", "sort_by": "popularity.desc"}
        if constraints.release_from:
            filters["primary_release_date.gte"] = f"{constraints.release_from}-01-01"
        if constraints.release_to:
            filters["primary_release_date.lte"] = f"{constraints.release_to}-12-31"

        groups = list(dict.fromkeys(tuple(match) for match in keyword_matches if isinstance(match, list) and match))
        if not groups:
            if all(isinstance(match, (httpx.HTTPError, ValueError, KeyError)) for match in keyword_matches):
                raise keyword_matches[0]
            return []
        batches = await asyncio.gather(*(
            self._get("discover/movie", with_keywords="|".join(map(str, group)), **filters)
            for group in groups
        ), return_exceptions=True)
        if all(isinstance(batch, (httpx.HTTPError, ValueError, KeyError)) for batch in batches):
            raise batches[0]
        hints = {}
        for batch in batches:
            if not isinstance(batch, dict):
                continue
            for row in batch.get("results", [])[:2]:
                title = row.get("title") or row.get("original_title")
                year = (row.get("release_date") or "")[:4]
                if isinstance(title, str) and title.strip() and len(title) <= 180 and year.isdecimal():
                    release_year = int(year)
                    if 1880 <= release_year <= 2200:
                        hints.setdefault((title.casefold(), release_year), FilmHint(title=title, year=release_year))
        return list(hints.values())

    async def lookup_movie(self, hint: FilmHint) -> FilmProjection | None:
        data = await self._lookup_details(hint)
        if data is None:
            return None
        release, kind = self._catalog_release(data)
        if not release:
            return None
        return await self._save_movie(data, release, kind)

    async def _lookup_details(self, hint: FilmHint) -> dict | None:
        if not self.options.enabled:
            return None
        search = await self._get(
            "search/movie",
            query=hint.title,
            include_adult="false",
            **({"year": hint.year} if hint.year else {}),
        )
        results = [
            item
            for item in search.get("results", [])[: self.options.search_result_limit]
            if film_title_key(hint.title) in {
                film_title_key(str(item.get("title", ""))),
                film_title_key(str(item.get("original_title", ""))),
            }
        ]
        matching_year = [item for item in results if str(item.get("release_date", ""))[:4] == str(hint.year)]
        candidates = matching_year or results
        if len(candidates) != 1:
            return None
        data = await self._get(
            f"movie/{int(candidates[0]['id'])}", append_to_response="credits,external_ids,release_dates"
        )
        if hint.year:
            release, _ = self._catalog_release(data)
            catalog_years = {str(data.get("release_date", ""))[:4], release[:4]}
            if str(hint.year) not in catalog_years:
                return None
        return data

    @staticmethod
    def _catalog_release(data: dict) -> tuple[str, str]:
        primary = data.get("release_date") or ""
        dates = [
            entry.get("release_date", "")[:10]
            for country in (data.get("release_dates") or {}).get("results", [])
            for entry in country.get("release_dates", []) if entry.get("type") == 1
        ]
        premieres = [date for date in dates if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)]
        if premieres:
            earliest = min(premieres)
            if not primary or earliest < primary:
                return earliest, "premiere"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", primary):
            return primary, "tmdb_release"
        return "", "tmdb_release"

    async def enrich_local_movie(self, film: FilmProjection) -> None:
        data = await self._lookup_details(FilmHint(title=film.original_title, year=film.year))
        if data is None:
            return
        if poster := tmdb_poster_path(data.get("poster_path")):
            await self.persistence.save_film_poster(film.id, poster)
            film.poster_path = poster
        source = f"{self.options.web_base_url.rstrip('/')}/{int(data['id'])}"
        await self._save_rating(film, data, source)

    async def _save_movie(self, data: dict, release: str, kind: str) -> FilmProjection:
        tmdb_id = int(data["id"])
        imdb_id = (data.get("external_ids") or {}).get("imdb_id")
        source = f"{self.options.web_base_url.rstrip('/')}/{tmdb_id}"
        facts = {
            "year": int(release[:4]),
            "countries": [item["iso_3166_1"] for item in data.get("production_countries", [])],
            "director": " / ".join(
                person["name"]
                for person in (data.get("credits") or {}).get("crew", [])
                if person.get("job") == "Director"
            ),
            "story": "",
            "reason": "",
            "language": data.get("original_language", ""),
            "overview": (data.get("overview") or "")[: self.options.overview_length],
        }
        if poster_path := tmdb_poster_path(data.get("poster_path")):
            facts["poster_path"] = poster_path
        film_id = await self.persistence.save_tmdb_film(
            tmdb_id,
            imdb_id,
            data["title"],
            data.get("original_title") or data["title"],
            facts,
            release,
            source,
            kind,
        )
        film = await self.local.lookup_movie(film_id)
        await self._save_rating(film, data, source)
        return film

    async def _save_rating(self, film: FilmProjection, data: dict, source: str) -> None:
        if data.get("vote_count", 0) > 0:
            await self.ratings.save(film.id, [
                Rating(
                    origin="TMDB",
                    metric="audience",
                    value=data["vote_average"],
                    scale=10,
                    count=data["vote_count"],
                    source_url=source,
                )
            ], "TMDB")
