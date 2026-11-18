# System Architecture

CineContext gathers evidence before generating a guide. To accept a film, the workflow requires evidence beyond catalog metadata that supports the film’s relevance to the brief. A guide or research timeline can cite only evidence stored for that run. Pydantic contracts, typed states, and validation enforce these rules.

## Layers

```text
React -> HTTP controllers -> Application services -> Domain
                                     ^                 ^
                                     |                 |
                        AI and SQLAlchemy adapters ----+
```

| Layer | Responsibility | Directory |
|---|---|---|
| Presentation | HTTP routes, request scope, statuses, and error mapping | [api](../backend/cinecontext/api) |
| Application | Parsing and research use cases, ports, and transaction boundaries | [application](../backend/cinecontext/application) |
| Domain | Films, runs, evidence, timeline entities, state rules, and repository protocols | [domain](../backend/cinecontext/domain) |
| Infrastructure | SQLAlchemy repositories, scoped persistence, and DI registration | [infrastructure](../backend/cinecontext/infrastructure) |
| AI | Model adapters, workflow nodes, search providers, and output validation | [ai](../backend/cinecontext/ai) |
| Framework | Configuration, DI, units of work, repository primitives, and FastAPI integration | [alcedo](../backend/alcedo) |

Application code uses the public [Alcedo exports](../backend/alcedo/__init__.py). Concrete adapters implement ports and are composed at startup. Domain and application protocols are independent of SQLAlchemy, model SDKs, and search providers.

## Composition and lifecycle

| Mode | Composition root | Provider and startup data |
|---|---|---|
| Production | [cinecontext/main.py](../backend/cinecontext/main.py) | `OpenAIModelProvider` for OpenAI-compatible and TACC connections |
| Demo | [cinecontext/mock/main.py](../backend/cinecontext/mock/main.py) | `MockModelProvider`, demo repositories, and idempotent fixture seeding |

Both roots use [bootstrap.py](../backend/cinecontext/bootstrap.py) to create FastAPI, resolve the database URL, configure Alcedo, register `/api`, and serve `frontend/dist` if present. Alcedo creates one scope per request. A background `CurationJob` creates its own scope. `StartupService.initialize()` marks stale queued or running work as interrupted. The demo startup service also seeds films, sources, passages, and ratings.

## Workflow and data flow

```text
POST /api/requests/parse -> structured criteria
POST /api/runs -> queued run -> BackgroundTasks -> CurationService
  PLAN -> DISCOVER -> ASSESS <-> FOLLOW_UP
    -> SELECT -> TIMELINE -> GUIDE -> RATINGS -> PUBLISH
GET /api/runs/{id} -> progress or completed, partial, or failed result
```

[workflow.py](../backend/cinecontext/ai/workflow.py) defines the runner and transition contracts. [nodes.py](../backend/cinecontext/ai/nodes.py) implements stateless nodes. The execution context stores one run’s criteria, plan, candidates, evidence, decisions, selected films, timeline, and guide. Each node mutates that context and returns one outcome. Each outcome must match exactly one transition. The runner allows at most 32 steps. Follow-up is limited to four queries per run and two per film. Guide generation can make two attempts. See the [AI workflow](ai-workflow.md) for recovery paths.

`submission_key` is unique in `runs`. A repeat with identical criteria returns the same run. Different criteria for a used key return a conflict. The API publishes the study snapshot in terminal states.

## Model adapters

[ai/ports.py](../backend/cinecontext/ai/ports.py) defines `ApplicationModel` and `ApplicationModelProvider`. Each run creates and closes its model client. The [production adapter](../backend/cinecontext/ai/openai_model.py) uses the OpenAI SDK and validates returned data with Pydantic. It supports Chat Completions and Responses. TACC can use an HTTP endpoint or [the SDK transport](../backend/cinecontext/ai/tacc_transport.py). A job that is still loading maps to an application-level availability error and HTTP 503. The [demo adapter](../backend/cinecontext/mock/ai/model.py) supplies deterministic responses.

The model receives numbered references for assessment and guide generation. The adapter translates valid numbers to internal UUIDs. The workflow checks whether the resulting evidence belongs to the run and, where applicable, to the particular film.

## Research tools

[research_tools/main.py](../backend/cinecontext/ai/research_tools/main.py) exposes the caller-facing protocols and aggregates local search, metadata, web search, and ratings in `ResearchToolsFacade`. `create_research_tools` selects providers and supplies shared persistence, HTTP, and configuration.

| Adapter | Responsibility |
|---|---|
| [local.py](../backend/cinecontext/ai/research_tools/local.py) | Film projections and PostgreSQL full-text passage search |
| [tmdb.py](../backend/cinecontext/ai/research_tools/tmdb.py) | Film identity, release metadata, poster paths, and TMDB audience scores |
| [brave.py](../backend/cinecontext/ai/research_tools/brave.py) | Brave LLM Context results |
| [wikipedia.py](../backend/cinecontext/ai/research_tools/wikipedia.py) | Article search and source excerpts |
| [duckduckgo.py](../backend/cinecontext/ai/research_tools/duckduckgo.py) | HTML search result parsing and snippet fallback |
| [crawler.py](../backend/cinecontext/ai/research_tools/crawler.py) | Public URL checks, redirect and size limits, and visible text extraction |
| [omdb.py](../backend/cinecontext/ai/research_tools/omdb.py) | IMDb, Rotten Tomatoes, and Metacritic snapshots keyed by IMDb ID |
| [ratings.py](../backend/cinecontext/ai/research_tools/ratings.py) | Rating provenance, timestamps, and persistence |
| [editorial.py](../backend/cinecontext/ai/research_tools/editorial.py) | Title-and-year matched curated readings and optional editorial lookup |

A Brave key takes priority for web evidence. The default TACC configuration uses Wikipedia otherwise. Other default configurations use DuckDuckGo with the crawler. TMDB and OMDb are enabled independently. The film readings endpoint verifies run membership in a short database scope, then queries its `FilmReadings` port after releasing that scope.

## Transactions and persistence

Alcedo’s `@transactional` uses `uow.execute()`. Nested calls within the same unit of work join the current transaction. Create, delete, and startup cleanup have explicit transaction boundaries.

A research workflow uses singleton `ScopedCurationPersistence`. Each call creates a scope, resolves `CurationPersistenceService`, performs a short database operation, and closes the scope. That lets the browser observe progress without holding transactions open during model calls or web requests. If a run fails, `CurationService` records a terminal failure and previously persisted research can still be inspected where available.

| Data | Tables | Owner |
|---|---|---|
| Film catalog and releases | `films`, `film_releases` | TMDB or the demo seed through the film repository |
| Long-lived documents | `sources`, `passages`, `film_passages` | Demo seed or corpus import, queried by local search |
| Research run | `runs` | Run application service and scoped persistence |
| Candidate decisions and evidence snapshots | `run_films`, `run_evidence` | Workflow persistence |
| Timeline and related films | `timeline_events`, `film_events`, `timeline_edges` | Timeline node and research repository |
| Tool audit | `tool_calls` | Logged workflow operations |
| Rating snapshots | `rating_snapshots` | TMDB, OMDb, or demo seed |

The [initial migration](../backend/migrations/versions/0001_initial.py) owns the PostgreSQL schema, including JSONB, TSVECTOR, GIN indexes, and composite keys. The app sets `database.create_schema: false`. Deleting a terminal run explicitly deletes its owned rows in dependency order. Shared catalog entries, source passages, and ratings remain available to other runs.

The release timeline comes from catalog metadata. Research timeline events reference selected films and stored evidence. A guide must cover each selected film once and list each once in viewing order. Rating failures can be recorded without invalidating research. Background tasks belong to the web process, so interrupted work needs a new request or a guide retry if the study snapshot was saved.
