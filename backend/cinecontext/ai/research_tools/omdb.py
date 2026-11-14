import httpx

from ...contracts import Rating
from ...settings import OmdbOptions
from ..types import FilmProjection
from .ratings import RatingStore


class OmdbRatingsProvider:
    def __init__(self, http: httpx.AsyncClient, options: OmdbOptions, store: RatingStore):
        self.http = http
        self.options = options
        self.store = store

    async def lookup(self, film: FilmProjection) -> list[Rating]:
        if not self.options.enabled or not film.imdb_id:
            return []
        response = await self.http.get(
            self.options.endpoint, params={"i": film.imdb_id, "apikey": self.options.api_key}
        )
        response.raise_for_status()
        data = response.json()
        if data.get("Response") != "True" or data.get("imdbID") != film.imdb_id:
            return []
        ratings = []
        for entry in data.get("Ratings", []):
            origin = entry["Source"]
            raw = entry["Value"]
            if origin == "Internet Movie Database" and raw.endswith("/10"):
                link = f"{self.options.imdb_base_url.rstrip('/')}/{film.imdb_id}/ratings/"
                ratings.append(
                    Rating(
                        origin="IMDb",
                        metric="audience",
                        value=float(raw.removesuffix("/10")),
                        scale=10,
                        source_url=link,
                    )
                )
            elif origin in {"Rotten Tomatoes", "Metacritic"}:
                suffix = "%" if origin == "Rotten Tomatoes" else "/100"
                if raw.endswith(suffix):
                    metric = "tomatometer" if origin == "Rotten Tomatoes" else "metascore"
                    ratings.append(
                        Rating(
                            origin=origin,
                            metric=metric,
                            value=float(raw.removesuffix(suffix)),
                            scale=100,
                            source_url=self.options.endpoint,
                        )
                    )
        await self.store.save(film.id, ratings, "OMDb")
        return ratings
