from alcedo import Configuration

from .ai.ports import ApplicationModelProvider
from .ai.openai_model import OpenAIModelProvider
from .ai.types import ModelMode
from .application.services import StartupService
from .bootstrap import ROOT, bootstrap_app
from .settings import ModelOptions


def create_app(database_url=None, serve_frontend=True):
    configuration = Configuration.load(
        ROOT / "backend" / "appsettings.yaml", env_prefix="APP"
    )
    model_settings = configuration.section("model")
    if model_settings.get("mode") != ModelMode.OPENAI:
        raise ValueError("production app requires model.mode=openai")

    def register(runtime):
        runtime.options.add(ModelOptions, "model")
        runtime.services.add_singleton(ApplicationModelProvider, OpenAIModelProvider)
        runtime.services.add_scoped(StartupService)

    return bootstrap_app(
        configuration, StartupService, register, database_url, serve_frontend,
        model_settings,
    )
