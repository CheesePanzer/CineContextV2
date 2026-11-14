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
from .demo_data import CONTEXT, FILMS, RATINGS


class DemoRepository(Protocol):
    async def seed(self) -> None: ...


class SqlAlchemyDemoRepository(SqlAlchemyRepository[Film]):
    entity_type = Film

    async def seed(self) -> None:
        existing_films = {film.slug: film for film in (await self.session.scalars(select(Film))).all()}
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
            if slug in existing_films:
                film = existing_films[slug]
                film.title = title
                film.facts = {**film.facts, "story": story, "reason": reason}
                if passage := existing_passages.get((src.id, slug)):
                    passage.body = body
                continue
            film = Film(slug=slug, title=title, original_title=title.split(" · ")[0],
                        facts={"theme": theme, "year": year, "countries": countries.split(),
                               "director": director, "story": story, "reason": reason})
            release_url, release_label, region, kind = url, str(year), "", "release_year"
            if slug == "ragnarok-2013":
                release_url, release_label, region, kind = (
                    "https://www.nb.no/filmografi/show?id=1177182", "2013-10-04", "NO", "premiere"
                )
            self.session.add(film)
            await self.session.flush()
            passage = Passage(source_id=src.id, locator=slug, body=body, search_terms=theme, kind="film")
            self.session.add(passage)
            await self.session.flush()
            self.session.add_all([
                FilmPassage(film_id=film.id, passage_id=passage.id),
                FilmRelease(film_id=film.id, label=release_label, year=year, region=region,
                            kind=kind, source_url=release_url),
            ])
            if slug == "ragnarok-2013":
                self.session.add(FilmRelease(film_id=film.id, label="2014-08-15", year=2014,
                                             region="US", kind="theatrical", source_url=url))
            for origin, metric, value, scale, count, link in RATINGS.get(slug, []):
                self.session.add(RatingSnapshot(
                    film_id=film.id, origin=origin, metric=metric,
                    payload={"value": value, "scale": scale, "count": count, "source_url": link,
                             "status": "available", "provider": "demo_snapshot", "as_of": "2026-11-05"},
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
