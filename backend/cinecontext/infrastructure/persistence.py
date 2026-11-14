from collections.abc import Sequence
from typing import Any
from uuid import UUID

from alcedo import ServiceScopeFactory, SqlAlchemyRepository, UnitOfWork, transactional
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from ..application.ports import CurationPersistence
from ..domain import (
    CandidateDecision,
    CurationRun,
    EvidenceSnapshot,
    Film,
    FilmEventLink,
    FilmPassage,
    FilmRelease,
    Passage,
    RatingSnapshot,
    Source,
    StoredTimelineEvent,
    TimelineEdge,
    ToolCall,
)
from ..domain.repositories import FilmRepository, ResearchRepository, RunRepository


class SqlAlchemyRunRepository(SqlAlchemyRepository[CurationRun]):
    entity_type = CurationRun

    async def list_recent(self, limit: int = 20):
        statement = select(CurationRun).order_by(CurationRun.created_at.desc()).limit(limit)
        return (await self.session.scalars(statement)).all()

    async def create_once(self, run: CurationRun) -> UUID | None:
        statement = insert(CurationRun).values(
            id=run.id,
            submission_key=run.submission_key,
            constraints=run.constraints,
            model_mode=run.model_mode,
            status=run.status,
            stage=run.stage,
            progress=run.progress,
        ).on_conflict_do_nothing(index_elements=[CurationRun.submission_key]).returning(CurationRun.id)
        return await self.session.scalar(statement)

    async def find_by_submission_key(self, submission_key: UUID):
        return await self.session.scalar(
            select(CurationRun).where(CurationRun.submission_key == submission_key)
        )

    async def interrupt_active(self, message: str) -> None:
        await self.session.execute(
            update(CurationRun).where(CurationRun.status.in_(("queued", "running"))).values(
                status="interrupted", stage="finished", error=message
            )
        )

    async def delete_with_data(self, run_id: UUID) -> None:
        # Delete aggregate children before the run because the initial schema has no ON DELETE CASCADE.
        for entity in (FilmEventLink, TimelineEdge, StoredTimelineEvent):
            await self.session.execute(delete(entity).where(entity.run_id == run_id))
        for entity in (ToolCall, EvidenceSnapshot, CandidateDecision):
            await self.session.execute(delete(entity).where(entity.run_id == run_id))
        await self.session.execute(delete(CurationRun).where(CurationRun.id == run_id))


class SqlAlchemyFilmRepository(SqlAlchemyRepository[Film]):
    entity_type = Film

    async def find_candidates(self, theme: str, limit: int = 10):
        statement = (
            select(Film.id)
            .where(Film.facts["theme"].astext == theme)
            .order_by(Film.created_at, Film.id)
            .limit(limit)
        )
        return (await self.session.scalars(statement)).all()

    async def get_projection(self, film_id: UUID):
        film = await self.get(film_id)
        releases = list((await self.session.scalars(
            select(FilmRelease).where(FilmRelease.film_id == film_id).order_by(FilmRelease.year)
        )).all())
        if not film or not releases:
            return None
        return {
            **film.facts,
            "id": str(film.id),
            "slug": film.slug,
            "title": film.title,
            "original_title": film.original_title,
            "tmdb_id": film.tmdb_id,
            "imdb_id": film.imdb_id,
            "releases": [
                {"label": item.label, "year": item.year, "region": item.region,
                 "kind": item.kind, "source_url": item.source_url}
                for item in releases
            ],
        }

    async def get_posters(self, film_ids: Sequence[UUID]):
        if not film_ids:
            return {}
        rows = await self.session.execute(
            select(Film.id, Film.facts["poster_path"].astext).where(Film.id.in_(film_ids))
        )
        return {str(film_id): path for film_id, path in rows if path}

    async def get_ratings(self, film_id: UUID):
        statement = select(RatingSnapshot).where(
            RatingSnapshot.film_id == film_id
        ).order_by(RatingSnapshot.origin)
        return (await self.session.scalars(statement)).all()

    async def save_tmdb_film(
        self, tmdb_id: int, imdb_id: str | None, title: str, original_title: str,
        facts: dict[str, Any], release: str, source_url: str,
    ) -> UUID:
        statement = insert(Film).values(
            slug=f"tmdb-{tmdb_id}", title=title, original_title=original_title,
            tmdb_id=tmdb_id, imdb_id=imdb_id, facts=facts,
        ).on_conflict_do_nothing(index_elements=[Film.tmdb_id]).returning(Film.id)
        film_id = await self.session.scalar(statement)
        if film_id is None:
            existing = await self.session.scalar(
                select(Film).where(Film.tmdb_id == tmdb_id).with_for_update()
            )
            if existing is None:
                raise RuntimeError("TMDB film conflict did not return the existing film.")
            poster_path = facts.get("poster_path")
            if poster_path and existing.facts.get("poster_path") != poster_path:
                existing.facts = {**existing.facts, "poster_path": poster_path}
            return existing.id
        self.session.add(FilmRelease(
            film_id=film_id, label=release, year=facts["year"], region="",
            kind="tmdb_release", source_url=source_url,
        ))
        return film_id

    async def save_ratings(self, film_id: UUID, ratings: Sequence[dict[str, Any]]) -> None:
        for rating in ratings:
            statement = insert(RatingSnapshot).values(
                film_id=film_id, origin=rating["origin"], metric=rating["metric"], payload=rating["payload"]
            )
            await self.session.execute(statement.on_conflict_do_update(
                index_elements=[RatingSnapshot.film_id, RatingSnapshot.origin, RatingSnapshot.metric],
                set_={"payload": statement.excluded.payload},
            ))


class SqlAlchemyResearchRepository(SqlAlchemyRepository[Source]):
    entity_type = Source

    async def search_passages(self, query: str, film_id: UUID | None, limit: int = 5):
        terms = func.websearch_to_tsquery("simple", query)
        statement = select(Passage, Source).join(Source).where(Passage.is_current)
        if film_id:
            statement = statement.join(FilmPassage).where(FilmPassage.film_id == film_id)
        else:
            statement = statement.where(Passage.kind != "film")
        statement = statement.where(Passage.search_vector.op("@@")(terms))
        statement = statement.order_by(func.ts_rank(Passage.search_vector, terms).desc(), Passage.id).limit(limit)
        return list((await self.session.execute(statement)).all())

    def add_tool_call(self, run_id, operation, status, elapsed_ms, detail):
        self.session.add(ToolCall(run_id=run_id, operation=operation, status=status,
                                  elapsed_ms=elapsed_ms, detail=detail))

    def add_evidence(self, snapshots):
        self.session.add_all(snapshots)

    def save_study(self, run, films, decisions, study):
        for film in films:
            decision = decisions[film["id"]]
            self.session.add(CandidateDecision(
                run_id=run.id, film_id=UUID(film["id"]),
                decision=decision["status"], explanation=decision,
            ))
        run.study_result = study

    async def save_timeline(self, run, study, views, valid_edges, evidence):
        run.study_result = study
        by_key = {}
        for axis, events in views.items():
            if axis == "release":
                continue
            for event in events:
                row = StoredTimelineEvent(run_id=run.id, event_key=event["id"], axis=axis,
                                          evidence_id=UUID(event["evidence_ids"][0]), data=event)
                by_key[event["id"]] = row
                self.session.add(row)
        await self.session.flush()
        evidence_by_id = {item["id"]: item for item in evidence}
        for row in by_key.values():
            event = row.data
            for film_id in set(event["film_ids"]):
                link_ref = next((ref for ref in event["evidence_ids"]
                                 if evidence_by_id[ref]["film_id"] == film_id), event["evidence_ids"][0])
                self.session.add(FilmEventLink(run_id=run.id, film_id=UUID(film_id), event_id=row.id,
                                               evidence_id=UUID(link_ref), relation=event["relation"]))
        stored_edges = set()
        for edge in valid_edges:
            pair = (edge.earlier, edge.later)
            if pair not in stored_edges:
                self.session.add(TimelineEdge(run_id=run.id, earlier_id=by_key[edge.earlier].id,
                                              later_id=by_key[edge.later].id, evidence_id=edge.evidence_ids[0]))
                stored_edges.add(pair)


class ScopedCurationPersistence(CurationPersistence):
    """Executes each typed persistence operation in a fresh Alcedo scope."""

    def __init__(self, scope_factory: ServiceScopeFactory):
        self.scope_factory = scope_factory

    async def advance_run(self, run_id, stage, message):
        async with self.scope_factory.create_scope() as scope:
            service = await scope.resolve(CurationPersistenceService)
            await service.advance_run(run_id, stage.value, message)

    async def finish_run(self, run_id, status, message, guide=None):
        async with self.scope_factory.create_scope() as scope:
            service = await scope.resolve(CurationPersistenceService)
            await service.finish_run(run_id, status.value, message, guide)

    async def record_tool_call(self, run_id, operation, status, elapsed_ms, detail):
        async with self.scope_factory.create_scope() as scope:
            service = await scope.resolve(CurationPersistenceService)
            await service.record_tool_call(run_id, operation, status, elapsed_ms, detail)

    async def add_evidence(self, run_id, rows):
        async with self.scope_factory.create_scope() as scope:
            service = await scope.resolve(CurationPersistenceService)
            return await service.add_evidence(run_id, rows)

    async def save_study(self, run_id, films, decisions, study):
        async with self.scope_factory.create_scope() as scope:
            service = await scope.resolve(CurationPersistenceService)
            await service.save_study(run_id, films, decisions, study)

    async def save_timeline(self, run_id, study, views, valid_edges, evidence):
        async with self.scope_factory.create_scope() as scope:
            service = await scope.resolve(CurationPersistenceService)
            await service.save_timeline(run_id, study, views, valid_edges, evidence)

    async def find_candidates(self, theme):
        async with self.scope_factory.create_scope() as scope:
            service = await scope.resolve(CurationPersistenceService)
            return await service.find_candidates(theme)

    async def get_film_projection(self, film_id):
        async with self.scope_factory.create_scope() as scope:
            service = await scope.resolve(CurationPersistenceService)
            return await service.get_film_projection(film_id)

    async def save_tmdb_film(self, tmdb_id, imdb_id, title, original_title, facts, release, source_url):
        async with self.scope_factory.create_scope() as scope:
            service = await scope.resolve(CurationPersistenceService)
            return await service.save_tmdb_film(
                tmdb_id, imdb_id, title, original_title, facts, release, source_url
            )

    async def search_passages(self, query, film_id=None, limit=5):
        async with self.scope_factory.create_scope() as scope:
            service = await scope.resolve(CurationPersistenceService)
            return await service.search_passages(query, film_id, limit)

    async def save_ratings(self, film_id, ratings):
        async with self.scope_factory.create_scope() as scope:
            service = await scope.resolve(CurationPersistenceService)
            await service.save_ratings(film_id, ratings)


class CurationPersistenceService:
    def __init__(self, runs: RunRepository, films: FilmRepository,
                 research: ResearchRepository, uow: UnitOfWork):
        self.runs = runs
        self.films = films
        self.research = research
        self.uow = uow

    @transactional
    async def advance_run(self, run_id, stage, message):
        run = await self.runs.get_for_update(run_id)
        if run is None:
            raise LookupError(run_id)
        run.advance(stage, message)

    @transactional
    async def finish_run(self, run_id, status, message, guide=None):
        run = await self.runs.get_for_update(run_id)
        if run is None:
            raise LookupError(run_id)
        if run.status in {"queued", "running"}:
            run.finish(status, message, guide)

    @transactional
    async def record_tool_call(self, run_id, operation, status, elapsed_ms, detail):
        self.research.add_tool_call(run_id, operation, status, elapsed_ms, detail)

    @transactional
    async def add_evidence(self, run_id, rows):
        if await self.runs.get_for_update(run_id) is None:
            raise LookupError(run_id)
        snapshots = [EvidenceSnapshot(run_id=run_id, **row) for row in rows]
        self.research.add_evidence(snapshots)
        return [{"id": str(item.id), "film_id": str(item.film_id) if item.film_id else None,
                 "source_url": item.source_url, "source_title": item.source_title,
                 "locator": item.locator, "body": item.body, "kind": item.kind}
                for item in snapshots]

    @transactional
    async def save_study(self, run_id, films, decisions, study):
        run = await self.runs.get_for_update(run_id)
        if run is None:
            raise LookupError(run_id)
        self.research.save_study(run, films, decisions, study)

    @transactional
    async def save_timeline(self, run_id, study, views, valid_edges, evidence):
        run = await self.runs.get_for_update(run_id)
        if run is None:
            raise LookupError(run_id)
        await self.research.save_timeline(run, study, views, valid_edges, evidence)

    async def find_candidates(self, theme):
        return await self.films.find_candidates(theme)

    async def get_film_projection(self, film_id):
        film = await self.films.get_projection(film_id)
        if film is None:
            raise LookupError("Missing film metadata")
        return film

    @transactional
    async def save_tmdb_film(self, tmdb_id, imdb_id, title, original_title, facts, release, source_url):
        return await self.films.save_tmdb_film(
            tmdb_id, imdb_id, title, original_title, facts, release, source_url
        )

    async def search_passages(self, query, film_id=None, limit=5):
        rows = await self.research.search_passages(query, film_id, limit)
        return [{"passage_id": passage.id, "film_id": film_id, "source_title": source.title,
                 "source_url": source.url, "locator": passage.locator, "body": passage.body,
                 "kind": passage.kind} for passage, source in rows]

    @transactional
    async def save_ratings(self, film_id, ratings):
        await self.films.save_ratings(film_id, ratings)


class CurationJob:
    """Runs curation after the request scope has ended."""

    def __init__(self, scope_factory: ServiceScopeFactory):
        self.scope_factory = scope_factory

    async def run(self, run_id: UUID, constraints):
        from ..ai.curation import CurationService

        async with self.scope_factory.create_scope() as scope:
            service = await scope.resolve(CurationService)
            await service.run(run_id, constraints)
