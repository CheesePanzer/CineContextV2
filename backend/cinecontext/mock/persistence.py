from typing import Protocol

from alcedo import SqlAlchemyRepository, UnitOfWork, transactional
from sqlalchemy import select

from ..domain import (
    Film,
    FilmPassage,
    FilmRelease,
    Passage,
    RatingSnapshot,
    Source,
)
from ..application.services import StartupService
from .demo_data import ADDITIONAL_THEMES, CONTEXT, FILMS, RATINGS, TMDB_SNAPSHOTS


class DemoRepository(Protocol):
    async def seed(self) -> None: ...


class SqlAlchemyDemoRepository(SqlAlchemyRepository[Film]):
    entity_type = Film

    async def seed(self) -> None:
        existing_films = {film.slug: film for film in (await self.session.scalars(select(Film))).all()}
        existing_tmdb_ids = {film.tmdb_id for film in existing_films.values() if film.tmdb_id}
        existing_ratings = set((await self.session.execute(
            select(RatingSnapshot.film_id, RatingSnapshot.origin, RatingSnapshot.metric)
        )).all())
        sources = {item.url: item for item in (await self.session.scalars(select(Source))).all()}
        existing_passages = {
            (passage.source_id, passage.locator): passage
            for passage in (await self.session.scalars(select(Passage).where(Passage.is_current))).all()
        }

        def source(url: str, title: str):
            if url not in sources:
                sources[url] = Source(url=url, title=title, usage_note="Demo paraphrase with source link")
                self.session.add(sources[url])
            elif sources[url].usage_note == "Demo paraphrase with source link":
                sources[url].title = title
            return sources[url]

        for theme, slug, title, year, countries, director, url, label, body, reason, story in FILMS:
            src = source(url, label)
            film = existing_films.get(slug)
            facts = {"story": story, "reason": reason, "overview": body}
            if slug in ADDITIONAL_THEMES:
                facts["additional_themes"] = ADDITIONAL_THEMES[slug]
            scores = list(RATINGS.get(slug, ()))
            tmdb_id = None
            if slug in TMDB_SNAPSHOTS:
                tmdb_id, poster_path, score, votes = TMDB_SNAPSHOTS[slug]
                facts["poster_path"] = poster_path
                scores.append(("TMDB", "audience", score, 10, votes, f"https://www.themoviedb.org/movie/{tmdb_id}"))
            if film is None:
                film = Film(slug=slug, title=title, original_title=title.split(" · ")[0],
                            facts={**facts, "theme": theme, "year": year,
                                   "countries": countries.split(), "director": director})
                self.session.add(film)
                await self.session.flush()
                passage = Passage(source_id=src.id, locator=slug, body=body, search_terms=theme, kind="film")
                self.session.add(passage)
                await self.session.flush()
                self.session.add(FilmPassage(film_id=film.id, passage_id=passage.id))
                if slug == "ragnarok-2013":
                    self.session.add_all([
                        FilmRelease(film_id=film.id, label="2013-10-04", year=2013, region="NO", kind="premiere",
                                    source_url="https://www.nb.no/filmografi/show?id=1177182"),
                        FilmRelease(film_id=film.id, label="2014-08-15", year=2014, region="US",
                                    kind="theatrical", source_url=url),
                    ])
                else:
                    self.session.add(FilmRelease(film_id=film.id, label=str(year), year=year,
                                                 region="", kind="release_year", source_url=url))
            else:
                film.title = title
                film.facts = {**film.facts, **facts}
                if passage := existing_passages.get((src.id, slug)):
                    passage.body = body
            if tmdb_id and film.tmdb_id is None and tmdb_id not in existing_tmdb_ids:
                film.tmdb_id = tmdb_id
                existing_tmdb_ids.add(tmdb_id)
            for origin, metric, value, scale, count, link in scores:
                if (film.id, origin, metric) in existing_ratings:
                    continue
                self.session.add(RatingSnapshot(
                    film_id=film.id, origin=origin, metric=metric,
                    payload={"value": value, "scale": scale, "count": count, "source_url": link,
                             "status": "available", "provider": "demo_snapshot",
                             "as_of": "2026-11-19" if origin == "TMDB" else "2026-11-05"},
                ))
        for theme, locator, title, url, body in CONTEXT:
            src = source(url, title)
            if passage := existing_passages.get((src.id, locator)):
                passage.body = body
                continue
            await self.session.flush()
            self.session.add(Passage(source_id=src.id, locator=locator, body=body,
                                     search_terms=theme, kind="context"))


class MockStartupService:
    def __init__(
        self,
        common: StartupService,
        demo: DemoRepository,
        uow: UnitOfWork,
    ):
        self.common = common
        self.demo = demo
        self.uow = uow

    @transactional
    async def initialize(self):
        await self.common.initialize()
        await self.demo.seed()
