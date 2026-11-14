import re

import httpx

from ...application.ports import CurationPersistence
from ...contracts import FilmHint, Rating
from ...settings import TmdbOptions
from ..types import FilmProjection
from .local import LocalResearchProvider
from .ratings import RatingStore


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
        response = await self.http.get(
            f"{self.options.api_base_url.rstrip('/')}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {self.options.access_token}"},
        )
        response.raise_for_status()
        return response.json()

    async def lookup_movie(self, hint: FilmHint) -> FilmProjection | None:
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
            if hint.title.casefold().strip()
            in {
                str(item.get("title", "")).casefold(),
                str(item.get("original_title", "")).casefold(),
            }
            and (not hint.year or str(item.get("release_date", "")).startswith(str(hint.year)))
        ]
        if len(results) != 1:
            return None
        data = await self._get(
            f"movie/{int(results[0]['id'])}", append_to_response="credits,external_ids"
        )
        release = data.get("release_date", "")
        if len(release) < 4 or not release[:4].isdigit():
            return None
        return await self._save_movie(data, release)

    async def _save_movie(self, data: dict, release: str) -> FilmProjection:
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
        )
        film = await self.local.lookup_movie(film_id)
        ratings = []
        if data.get("vote_count", 0) > 0:
            ratings.append(
                Rating(
                    origin="TMDB",
                    metric="audience",
                    value=data["vote_average"],
                    scale=10,
                    count=data["vote_count"],
                    source_url=source,
                )
            )
        await self.ratings.save(film.id, ratings, "TMDB")
        return film
