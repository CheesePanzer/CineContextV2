from alcedo import Configuration

from .ai.ports import ApplicationModelProvider
from .ai.openai_model import OpenAIModelProvider
from .ai.types import ModelMode
from .application.ports import TaccJobControl
from .application.services import StartupService, TaccJobApplicationService
from .bootstrap import ROOT, bootstrap_app
from .infrastructure.tacc_jobs import TaccJobs
from .settings import ModelOptions


def create_app(database_url=None, serve_frontend=True):
    configuration = Configuration.load(
        ROOT / "backend" / "appsettings.yaml", env_prefix="APP"
    )
    model_settings = configuration.section("model")
    if model_settings.get("mode") not in {ModelMode.OPENAI, ModelMode.TACC}:
        raise ValueError("production app requires model.mode=openai or tacc")
    if model_settings["mode"] == ModelMode.TACC:
        ModelOptions.model_validate(model_settings)

    def register(runtime):
        runtime.options.add(ModelOptions, "model")
        runtime.services.add_singleton(ApplicationModelProvider, OpenAIModelProvider)
        runtime.services.add_singleton(TaccJobControl, TaccJobs)
        runtime.services.add_scoped(TaccJobApplicationService)
        runtime.services.add_scoped(StartupService)

    return bootstrap_app(
        configuration, StartupService, register, database_url, serve_frontend,
        model_settings,
    )
