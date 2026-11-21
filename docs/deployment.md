# Deployment and Configuration

The repository launchers serve the app on localhost. They are intended for development and course demonstrations. The [README](../README.md) has the shortest path to a running demo.

## Prerequisites

Use Python 3.14, Node.js and npm, and PostgreSQL. The frontend uses its npm lockfile. The launchers use `initdb` and `pg_ctl` when no `DATABASE_URL` is supplied. On Windows, the launcher also searches the PostgreSQL installation under Program Files.

```bash
python3.14 -m venv .venv
./.venv/bin/python -m pip install -e backend
npm --prefix frontend ci
```

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .\backend
npm --prefix .\frontend ci
```

`start.sh` uses `CINECONTEXT_PYTHON` when set and otherwise looks for a Conda environment named `cinecontext`. `start.ps1` checks that variable, the repository's `.venv`, then `python` on PATH.

## PostgreSQL

Without a database URL, the launchers initialize `.runtime/postgres`, store a generated password in `.runtime/db-password`, and start the service at `127.0.0.1:55438` by default. The default database is `postgres` and its role is `cinecontext`. Override the port with `CINECONTEXT_DB_PORT`.

You can also start PostgreSQL separately. For example, create a local Docker database with placeholder credentials.

```bash
docker run --name cinecontext-postgres \
  -e POSTGRES_PASSWORD='<admin-password>' \
  -p 127.0.0.1:5432:5432 -d postgres:17

docker exec -it cinecontext-postgres psql -U postgres \
  -c "CREATE ROLE cinecontext LOGIN PASSWORD '<app-password>';"
docker exec -it cinecontext-postgres psql -U postgres \
  -c "CREATE DATABASE cinecontext OWNER cinecontext;"
```

Set `DATABASE_URL` to `postgresql+asyncpg://cinecontext:<encoded-app-password>@127.0.0.1:5432/cinecontext`. Percent-encode only the username or password components if they contain reserved URL characters. For example, `p@ss:word` becomes `p%40ss%3Aword`.

The launchers apply Alembic migrations automatically. To run migrations separately, configure `DATABASE_URL` and run `python -m alembic -c backend/alembic.ini upgrade head` from the repository root, or `python -m alembic upgrade head` from `backend/`. Migrations rely on PostgreSQL types and full-text indexes.

## Launch the app

```bash
CINECONTEXT_PYTHON="$PWD/.venv/bin/python" ./scripts/start.sh
```

```powershell
.\scripts\start.ps1
```

Both launchers load the root `.env`, check dependencies, prepare PostgreSQL or use the supplied URL, run migrations, install frontend dependencies, and build `frontend/dist`. FastAPI serves the API and the built frontend together. The default app mode is `mock`.

## TACC Qwen

First configure the course `tcloud` SDK on the machine running FastAPI. Configure production, TACC, and optional film metadata access in the root `.env`.

```dotenv
CINECONTEXT_APP=production
APP_ENVIRONMENT=tacc
APP__MODEL__TCLOUD_BIN=tcloud
APP__TOOLS__TMDB__ACCESS_TOKEN=
```

`TCLOUD_BIN` also accepts an absolute executable path. To submit the first job and start the local app on Bash, run the following.

```bash
./scripts/start.sh --submit-tacc
```

This records the job ID, submission time, and wall time. Later launches can reuse that job. To attach to a specific job, use `./scripts/start.sh JOB_ID`, or set `APP__MODEL__TACC_JOB_ID` in `.env` before starting through PowerShell.

```bash
(cd deploy/tacc && tcloud submit)
tcloud ps -j JOB_ID
tcloud cat slurm_log/cinecontext-qwen-JOB_ID.log
```

The deployment requests two GPUs and one hour of runtime. The sidebar polls about every 15 seconds. It reports queue wait, loading, readiness, and remaining runtime when a local submission record is available. **TACC ready** or `CINECONTEXT_QWEN_READY` in the job log indicates that the model is serving. Each job can handle multiple requests. A model request timeout can return HTTP 504 while the job remains available for future requests.

When a tracked job ends, use **Request TACC GPUs** to submit a replacement. **Restart · 1 hour** cancels a tracked live or queued job and submits a replacement after cancellation is confirmed. This can interrupt ongoing requests, and the new job goes through the queue again. Concurrent attempts to replace the same job are deduplicated. If the app cannot confirm cancellation, refresh status before retrying. If the previous job exited because its allocated GPUs lacked free memory, the app reads its Slurm log and temporarily excludes that node for 30 minutes before submitting the replacement. You can also set `CINECONTEXT_TACC_EXCLUDE_NODES` to a comma-separated list of node names to exclude manually.

`PD (Resources)` in `tcloud ps` means the job is waiting for GPUs. If a job exits before becoming ready, inspect the job log for the free-memory check. For manual submission, `- exclude=NODE_NAME` under `job.general` in the submission configuration skips a specific node. `tcloud cancel -j JOB_ID` ends a job after a demo. `service_supervisor.py` shuts down the request worker and the vLLM process group, then logs `CINECONTEXT_CLEANUP` and GPU memory statistics.

The SDK exchanges request and response files under the submitting account. If your backend can reach the course HTTP endpoint, set `APP__MODEL__BASE_URL` to the endpoint ending in `/v1` and provide any required credentials. Each teammate running a local backend needs an authorized TACC account. Browsers using a shared backend use that backend's connection.

TACC defaults to Wikipedia for evidence. A configured Brave key takes priority. The workflow can use corpus data already in PostgreSQL. Start the demo against a separate database to inspect its fixture corpus. When running multiple versions, use distinct ports or databases, for example web port `8766` and PostgreSQL port `55439` for V2.

## DeepSeek / OpenAI-compatible example

Set the base URL, key, and model ID supplied for your account. For example, a Chat Completions configuration can use the following settings.

```dotenv
CINECONTEXT_APP=production
APP__MODEL__API_STYLE=chat_completions
APP__MODEL__BASE_URL=https://api.deepseek.com
APP__MODEL__API_KEY=<api-key>
APP__MODEL__MODEL_ID=<deepseek-model-id>
APP__MODEL__TIMEOUT_SECONDS=180
APP__MODEL__MAX_TOKENS=6000
APP__MODEL__TEMPERATURE=0.2
APP__MODEL__JSON_RESPONSE=true
APP__MODEL__THINKING=disabled
```

Use `APP__MODEL__API_STYLE=responses` for endpoints implementing the Responses API. That path supports a prompt-cache key when enabled. The chat-only generation options apply to Chat Completions.

## Configuration precedence

The launchers inherit the process environment and then load `.env`, so a value in `.env` can override a shell setting. Use simple assignments that Bash and PowerShell can both read. The application loads these sources in increasing precedence.

1. `backend/appsettings.yaml` or `backend/appsettings.mock.yaml`.
2. The matching environment overlay when `APP_ENVIRONMENT` is set and the file exists.
3. `APP__...` environment variables. Double underscores delimit nested keys.

The database URL is resolved from an explicit `create_app(database_url=...)` argument, `DATABASE_URL`, or the merged application configuration, in that order. `CINECONTEXT_APP` selects the composition root.

| Launch variable | Purpose |
|---|---|
| `CINECONTEXT_APP` | `mock` by default, or `production` |
| `CINECONTEXT_PYTHON` | Python executable override |
| `CINECONTEXT_PORT` | App port, default `8765` |
| `CINECONTEXT_DB_PORT` | Port for an automatically started PostgreSQL, default `55438` |
| `DATABASE_URL` | Async SQLAlchemy PostgreSQL URL |
| `CINECONTEXT_TEST_URL` | Base URL of the demo smoke target, default `http://127.0.0.1:8765` |
| `APP_ENVIRONMENT` | Configuration overlay, such as `tacc` |

| Model variable | Purpose |
|---|---|
| `APP__MODEL__MODE` | `openai` by default in production, or `tacc` |
| `APP__MODEL__API_STYLE` | `chat_completions` by default, or `responses` |
| `APP__MODEL__BASE_URL`, `APP__MODEL__API_KEY`, `APP__MODEL__MODEL_ID` | Endpoint, credentials, and model identifier |
| `APP__MODEL__TACC_JOB_ID`, `APP__MODEL__TCLOUD_BIN` | TACC job and SDK executable |
| `APP__MODEL__TIMEOUT_SECONDS` | Default `60`, with `420` in the TACC overlay |
| `APP__MODEL__MAX_RETRIES` | SDK retries, default `1` |
| `APP__MODEL__MAX_TOKENS`, `APP__MODEL__TEMPERATURE` | Optional Chat Completions generation settings |
| `APP__MODEL__JSON_RESPONSE` | Chat JSON object mode, default `true` |
| `APP__MODEL__THINKING` | `default`, `enabled`, or `disabled` for compatible chat endpoints |
| `APP__MODEL__PROMPT_CACHE` | Enable a Responses prompt-cache key, default `false` |
| `APP__MODEL__STRUCTURED_OUTPUT_RETRIES` | Retries for invalid structured data, default `1`, range 0–3 |

### Tool options

Add the `APP__TOOLS__` prefix to the keys below. Values and their bounds are defined in [settings.py](../backend/cinecontext/settings.py).

| Suffix | Default or behavior |
|---|---|
| `TIMEOUT_SECONDS` | `15` for shared HTTP requests |
| `TMDB__ACCESS_TOKEN` | Enables film ID, metadata, posters, and TMDB audience scores |
| `TMDB__API_BASE_URL`, `TMDB__WEB_BASE_URL` | `https://api.themoviedb.org/3`, `https://www.themoviedb.org/movie` |
| `TMDB__SEARCH_RESULT_LIMIT`, `TMDB__OVERVIEW_LENGTH` | `8` and `1000` |
| `BRAVE__API_KEY`, `BRAVE__ENDPOINT` | Enables Brave LLM Context and controls its endpoint |
| `BRAVE__QUERY_LENGTH`, `BRAVE__MAXIMUM_URLS` | `600` and `4` |
| `BRAVE__MAXIMUM_TOKENS`, `BRAVE__MAXIMUM_TOKENS_PER_URL` | `2048` and `512` |
| `BRAVE__SNIPPETS_PER_URL`, `BRAVE__EXCERPT_LENGTH` | `2` and `1400` |
| `DUCKDUCKGO__ENABLED`, `DUCKDUCKGO__ENDPOINT` | `true` and `https://html.duckduckgo.com/html/` |
| `DUCKDUCKGO__QUERY_LENGTH`, `DUCKDUCKGO__RESULT_LIMIT` | `600` and `4` |
| `CRAWLER__ENABLED`, `CRAWLER__MAX_PAGES` | `true` and `4` |
| `CRAWLER__RESPONSE_BYTE_LIMIT`, `CRAWLER__EXCERPT_LENGTH` | `1000000` and `1400` |
| `CRAWLER__USER_AGENT`, `CRAWLER__MAX_REDIRECTS` | `CineContext/0.1 (+https://github.com/)` and `3` |
| `OMDB__API_KEY`, `OMDB__ENDPOINT`, `OMDB__IMDB_BASE_URL` | Optional key, `https://www.omdbapi.com/`, and `https://www.imdb.com/title` |

Brave takes priority when configured. The default non-TACC app uses DuckDuckGo and the optional crawler. The crawler follows public HTTP(S) links, checks each redirect, and limits content types and size. A search snippet may be used when a page cannot be fetched. The free search route can be rate-limited and follows site access restrictions. OMDb enriches matching IMDb IDs with IMDb, Rotten Tomatoes, and Metacritic snapshots. Editorial readings and scores have separate sources.

## Health

```bash
curl http://127.0.0.1:8765/api/health
```

Check `status`, `model_mode`, `model_id`, `model_status`, and the tool-enabled flags. A configured tool flag describes configuration. A TACC-ready status describes the current model job.

## Logs and diagnostics

The `cinecontext` logger writes to stderr and to `.runtime/logs/cinecontext.log`. It rotates at 10 MB by default and keeps five backups.

| Variable | Default or behavior |
|---|---|
| `APP__LOGGING__LEVEL` | `INFO`, with `DEBUG`, `WARNING`, and `ERROR` available |
| `APP__LOGGING__CONSOLE_ENABLED`, `APP__LOGGING__FILE_ENABLED` | Both default to `true` |
| `APP__LOGGING__PATH` | `.runtime/logs/cinecontext.log`, relative to the repository root |
| `APP__LOGGING__MAX_BYTES`, `APP__LOGGING__BACKUP_COUNT` | `10000000` and `5` |
| `APP__LOGGING__INCLUDE_TECHNICAL_FAILURE_DETAIL` | `true` to attach a sanitized and truncated detail to `run.error` |
| `APP__LOGGING__CAPTURE_MODEL_RESPONSES` | `false`. Enable only for local diagnostics. |
| `APP__LOGGING__MODEL_RESPONSE_PATH` | `.runtime/logs/model-responses` |

Follow the log with `tail -n 100 -f .runtime/logs/cinecontext.log` or PowerShell's `Get-Content .\.runtime\logs\cinecontext.log -Wait -Tail 100`. Search by run ID or diagnostic ID first. The file log has stack traces and timing. Database `tool_calls` contain per-operation statuses. `run.error` contains the user-facing message and diagnostic ID.

Keep credentials, prompts, queries, source excerpts, response bodies, and headers out of ordinary logs. Set `INCLUDE_TECHNICAL_FAILURE_DETAIL` to `false` for deployments that need shorter user-facing errors. Captured model responses can contain user and source data. Keep them local and turn off capture after debugging. If `finish_reason=length` coincides with `content_length=0` and high reasoning token use, check the structured response's token budget and whether that endpoint supports disabling thinking.

## Verify, stop, and restart

Run the [README checks](../README.md#reproduce-the-software-checks) against an isolated demo database for reproducible results. The smoke suite creates and polls actual API runs.

Use Ctrl+C to stop Uvicorn. An automatically started database continues running. To stop it explicitly, run `pg_ctl -D .runtime/postgres -m fast stop`. For Docker, use `docker stop cinecontext-postgres` and `docker start cinecontext-postgres`. A web restart reruns migrations and builds, and marks unfinished research interrupted. Use a new submission key to start a new run.

| Symptom | Check |
|---|---|
| `initdb` or `pg_ctl` not found | Install the tools, put them on PATH, or supply an existing database URL. |
| Database URL or authentication error | Check permissions, credentials, and URL encoding. |
| Alembic cannot import application modules | Install the editable package and use the documented config path. |
| Model credentials or mode error | Check `APP_ENVIRONMENT`, model settings, and whether `.env` overwrites your shell values. |
| Tool status disabled | Check the provider token or key and restart the app. |
| Web result has no article body | The site may reject the crawler, exceed its limits, or serve unsupported content. |
| Error with a diagnostic ID | Find that ID in the application log and inspect tool activity for the run. |
| Stale frontend | Rebuild with `npm --prefix frontend run build` before starting FastAPI manually. |
| Port unavailable | Change `CINECONTEXT_PORT` or `CINECONTEXT_DB_PORT`. |
