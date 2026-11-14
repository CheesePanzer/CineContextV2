from alcedo import Configuration

from ..ai.ports import ApplicationModelProvider
from ..ai.types import ModelMode
from ..application.services import StartupService
from ..bootstrap import ROOT, bootstrap_app
from .ai.model import MockModelProvider
from .persistence import DemoRepository, MockStartupService, SqlAlchemyDemoRepository


def create_app(database_url=None, serve_frontend=True):
    configuration = Configuration.load(
        ROOT / "backend" / "appsettings.mock.yaml", env_prefix="APP"
    )
    model_settings = configuration.section("model")
    model_settings["mode"] = ModelMode.MOCK.value

    def register(runtime):
        services = runtime.services
        services.add_singleton(ApplicationModelProvider, MockModelProvider)
        services.add_scoped(StartupService)
        services.add_scoped(DemoRepository, SqlAlchemyDemoRepository)
        services.add_scoped(MockStartupService)

    return bootstrap_app(
        configuration, MockStartupService, register, database_url, serve_frontend,
        model_settings,
    )
