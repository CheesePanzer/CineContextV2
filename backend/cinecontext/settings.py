from alcedo import OptionsBase
from enum import StrEnum
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, field_validator, model_validator

from .ai.types import ModelApiStyle, ModelMode, ThinkingMode


class LoggingLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LoggingOptions(OptionsBase):
    level: LoggingLevel = LoggingLevel.INFO
    console_enabled: bool = True
    file_enabled: bool = True
    path: str = ".runtime/logs/cinecontext.log"
    max_bytes: int = Field(default=10_000_000, ge=1)
    backup_count: int = Field(default=5, ge=0)
    include_technical_failure_detail: bool = True
    capture_model_responses: bool = False
    model_response_path: str = ".runtime/logs/model-responses"


class ModelOptions(OptionsBase):
    mode: ModelMode = ModelMode.OPENAI
    api_style: ModelApiStyle = ModelApiStyle.CHAT_COMPLETIONS
    base_url: str | None = None
    api_key: str | None = None
    model_id: str | None = None
    tacc_job_id: str | None = Field(default=None, pattern=r"^[0-9]+$")
    tcloud_bin: str = "tcloud"
    timeout_seconds: float = Field(default=60, gt=0)
    max_retries: int = Field(default=1, ge=0)
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    json_response: bool = True
    thinking: ThinkingMode = ThinkingMode.DEFAULT
    prompt_cache: bool = False
    structured_output_retries: int = Field(default=1, ge=0, le=3)

    @field_validator("tacc_job_id", mode="before")
    @classmethod
    def normalize_job_id(cls, value):
        return str(value) if type(value) is int else value

    @model_validator(mode="after")
    def validate_credentials(self):
        if self.mode == ModelMode.TACC:
            if self.api_style != ModelApiStyle.CHAT_COMPLETIONS:
                raise ValueError("TACC requires chat_completions")
            if self.tacc_job_id and self.base_url:
                raise ValueError("Set either model.tacc_job_id or model.base_url")
            if not self.tacc_job_id:
                endpoint = urlsplit(self.base_url or "")
                if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.path.rstrip("/") != "/v1":
                    raise ValueError("TACC requires a job ID or an HTTP base URL ending in /v1")
            self.model_id = self.model_id or "qwen3.8-27b"
            return self
        if self.tacc_job_id:
            raise ValueError("model.tacc_job_id requires model.mode=tacc")
        missing = [name for name in ("api_key", "model_id") if not getattr(self, name)]
        if missing:
            raise ValueError(f"model configuration requires {', '.join(missing)}")
        return self


class TmdbOptions(BaseModel):
    access_token: str | None = None
    api_base_url: str = "https://api.themoviedb.org/3"
    web_base_url: str = "https://www.themoviedb.org/movie"
    search_result_limit: int = Field(default=8, ge=1, le=20)
    overview_length: int = Field(default=1000, ge=1)

    @property
    def enabled(self) -> bool:
        return bool(self.access_token)


class BraveOptions(BaseModel):
    api_key: str | None = None
    endpoint: str = "https://api.search.brave.com/res/v1/llm/context"
    query_length: int = Field(default=600, ge=1)
    maximum_urls: int = Field(default=4, ge=1)
    maximum_tokens: int = Field(default=2048, ge=1)
    maximum_tokens_per_url: int = Field(default=512, ge=1)
    snippets_per_url: int = Field(default=2, ge=1)
    excerpt_length: int = Field(default=1400, ge=1)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


class DuckDuckGoOptions(BaseModel):
    enabled: bool = True
    endpoint: str = "https://html.duckduckgo.com/html/"
    query_length: int = Field(default=600, ge=1)
    result_limit: int = Field(default=4, ge=1, le=20)


class CrawlerOptions(BaseModel):
    enabled: bool = True
    max_pages: int = Field(default=4, ge=1, le=20)
    response_byte_limit: int = Field(default=1_000_000, ge=1)
    excerpt_length: int = Field(default=1400, ge=1)
    user_agent: str = Field(default="CineContext/0.1 (+https://github.com/)", min_length=1)
    max_redirects: int = Field(default=3, ge=0, le=10)


class OmdbOptions(BaseModel):
    api_key: str | None = None
    endpoint: str = "https://www.omdbapi.com/"
    imdb_base_url: str = "https://www.imdb.com/title"

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


class ToolsOptions(OptionsBase):
    timeout_seconds: float = Field(default=15, gt=0)
    wikipedia_enabled: bool = False
    tmdb: TmdbOptions = Field(default_factory=TmdbOptions)
    brave: BraveOptions = Field(default_factory=BraveOptions)
    duckduckgo: DuckDuckGoOptions = Field(default_factory=DuckDuckGoOptions)
    crawler: CrawlerOptions = Field(default_factory=CrawlerOptions)
    omdb: OmdbOptions = Field(default_factory=OmdbOptions)
