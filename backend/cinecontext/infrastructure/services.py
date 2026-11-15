from alcedo import AlcedoRuntime

from ..application.services import FilmReadingsApplicationService, ParseApplicationService, RunApplicationService
from ..application.ports import CurationPersistence, FilmReadings, ReferenceSearch
from ..ai.curation import CurationService
from ..ai.research_tools.editorial import WebFilmReadings
from ..ai.research_tools.reference_search import WebReferenceSearch
from ..domain.repositories import FilmRepository, ResearchRepository, RunRepository
from .persistence import (
    CurationPersistenceService,
    ScopedCurationPersistence,
    SqlAlchemyFilmRepository,
    SqlAlchemyResearchRepository,
    SqlAlchemyRunRepository,
)


def add_cinecontext_services(runtime: AlcedoRuntime) -> None:
    services = runtime.services
    services.add_scoped(RunRepository, SqlAlchemyRunRepository)
    services.add_scoped(FilmRepository, SqlAlchemyFilmRepository)
    services.add_scoped(ResearchRepository, SqlAlchemyResearchRepository)
    services.add_scoped(CurationPersistenceService)
    services.add_singleton(CurationPersistence, ScopedCurationPersistence)
    services.add_singleton(ReferenceSearch, WebReferenceSearch)
    services.add_scoped(CurationService)
    services.add_scoped(ParseApplicationService)
    services.add_scoped(RunApplicationService)
    services.add_singleton(FilmReadings, WebFilmReadings)
    services.add_scoped(FilmReadingsApplicationService)
