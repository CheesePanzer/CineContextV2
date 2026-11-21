# Development and API Guide

## Local development

Install `backend` in editable mode from the project environment. This makes both `cinecontext` and the vendored `alcedo` package available to the tests. Use public framework exports such as `from alcedo import UnitOfWork, transactional`. The public interface is declared in [alcedo/__init__.py](../backend/alcedo/__init__.py). [AlcedoButPython](https://github.com/CheesePanzer/AlcedoButPython) is the upstream DDD framework and takes cues from ASP.NET Core. Ask for human review when modifying the framework itself.

Keep HTTP parsing, dependency injection, and response mapping in `api/`. Put use cases and transaction boundaries in `application/` and database implementation details in `infrastructure/`. Integrate external services through a protocol, adapter, and composition root. Use `StrEnum` for new persisted states, evidence kinds, and operation names. Cover their serialized values in regression tests.

```bash
npm --prefix frontend run dev
```

The integrated launchers build `frontend/dist` and serve it through FastAPI. The frontend uses relative `/api` requests. Provide same-origin routing or a proxy to the backend when using the standalone Vite server.

## API

Open `/docs` for the FastAPI schema when the service is running.

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/health` | Returns model mode, provider configuration, and TACC wait or remaining time |
| POST | `/api/tacc/jobs` | Requests a TACC job from the local app |
| POST | `/api/requests/parse` | Converts a question to structured criteria |
| POST | `/api/runs` | Creates and schedules a run, returning HTTP 202 |
| GET | `/api/runs` | Returns the most recent 20 runs |
| GET | `/api/runs/{run_id}` | Returns progress and the result when available |
| DELETE | `/api/runs/{run_id}` | Removes a terminal run and its owned records |
| POST | `/api/runs/{run_id}/retry-guide` | Recreates the guide from saved research |
| GET | `/api/runs/{run_id}/films/{film_id}` | Returns film metadata, guide details, and evidence |
| GET | `/api/runs/{run_id}/films/{film_id}/readings` | Returns saved criticism after checking film membership. Add `?live=true` to look up missing publishers after the database scope closes. |
| GET | `/api/films/{film_id}/ratings` | Returns persisted rating snapshots |

Sending the same `submission_key` with the same criteria returns the existing run. Changing the criteria for that key returns HTTP 409.

A guide retry requires a partial, failed, or interrupted run with selected films and evidence. The transaction locks and claims the run, sets `running/generating`, and clears the prior guide and error. The application uses the `GuideRetrySnapshot` contract from `ai/resume.py`. The background task runs the guide and publication steps using the archived search, assessment, timeline, and ratings.

For `POST /api/tacc/jobs`, pass `previous_job_id` and optionally `restart: true`. The latter verifies cancellation before submitting another job. Concurrent requests share the submission task inside the app process, and a request referring to an earlier job can return the new job already submitted. Localhost and browser Origin checks apply. The frontend polls for updates and offers a manual status refresh.

DELETE locks the targeted run with `SELECT ... FOR UPDATE`. It returns HTTP 404 for a missing run or HTTP 409 for one that is still queued or running. The repository deletes run-owned links, timeline events, tool calls, evidence, and decisions in dependency order. Films, sources, passages, and ratings are shared.

## Database migrations

Create a new revision in `backend/migrations/versions/` for schema changes. The current revision is `0001`. The app defaults to `database.create_schema: false` and uses Alembic for schema management.

```bash
DATABASE_URL='postgresql+asyncpg://cinecontext:<encoded-password>@127.0.0.1:5432/cinecontext' \
  ./.venv/bin/python -m alembic -c backend/alembic.ini upgrade head
```

Inspect generated migrations against the domain mappings and SQL for JSONB, TSVECTOR, computed columns, GIN indexes, and composite foreign keys. Check both upgrade and downgrade when changing the schema.

## Testing

```bash
./.venv/bin/python -m unittest tests.test_ai_workflow
npm --prefix frontend ci
npm --prefix frontend run build
```

The unit tests cover workflow transitions, serialization, timeline and guide validation, provider selection, crawler behavior, and deletion constraints. Run the API smoke suite separately against a running demo instance.

```bash
CINECONTEXT_TEST_URL=http://127.0.0.1:8765 ./.venv/bin/python tests/smoke.py
```

The smoke suite creates runs in the connected database for medieval European history, the Romanian New Wave, Italian neorealism, and MCU worldbuilding. It checks selected films, timeline ordering, and citations against fixture data. On the Romanian and Italian cases it also checks that the detail endpoint returns a TMDB rating snapshot and articles from Criterion, BFI, and Senses of Cinema. The repository has no committed browser suite or CI tests for live providers and multi-worker recovery.

## Where to make changes

| Change | Entry point |
|---|---|
| HTTP use case | `api/controllers.py`, `application/services.py`, and the relevant contract or port |
| Domain state or entity | `domain/entities.py`, repository protocol, migration, and infrastructure implementation |
| Workflow step | `ai/workflow.py`, `ai/nodes.py`, and transition tests |
| Model provider | `ai/ports.py` and the composition root |
| Research provider | Adapter in `ai/research_tools/` and the factory in its `main.py` |
| Settings | Typed `settings.py`, appsettings defaults, `.env.example`, and deployment docs |
| Demo scenario | `mock/demo_data.py`, `mock/ai/model.py`, and smoke assertions |

Before merging, verify that network calls remain outside database transactions, all workflow outcomes and loop limits are explicit, references belong to the correct run or film, and configuration names agree across code and documentation. Run the relevant tests and `git diff --check`.
