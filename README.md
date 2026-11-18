# CineContext

CineContext turns a film research question into a viewing guide with sources. It looks up candidate films, checks them against the brief, and organizes the results by viewing order, release date, story period, mythology, or historical context. Select a film to see its synopsis, audience ratings, and available criticism.

The project includes a deterministic demo and a production mode for live research. Both modes use the same PostgreSQL schema, workflow, and API. The demo runs without TACC access or API keys.

## Getting Started Instructions

Use Python 3.14, Node.js 22.12 or later, npm, and PostgreSQL. Put `initdb` and `pg_ctl` on PATH to let the launcher create a local database. You can also set `DATABASE_URL` to an existing PostgreSQL instance. See [deployment and configuration](docs/deployment.md) for Docker and detailed setup.

### Start the demo

For macOS or Linux, run these commands from the repository root.

```bash
python3.14 -m venv .venv
CINECONTEXT_PYTHON="$PWD/.venv/bin/python" ./scripts/start.sh
```

For Windows PowerShell, run the following.

```powershell
py -3.14 -m venv .venv
.\scripts\start.ps1
```

If the PowerShell execution policy blocks the launcher, use the following process-specific invocation.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

The launcher installs missing backend dependencies, migrates PostgreSQL, installs frontend dependencies with `npm ci`, builds the frontend, and starts the app. Open [CineContext](http://127.0.0.1:8765). Use a fresh checkout or set the local configuration to demo mode if an existing `.env` selects live providers. Bash can also use a Conda environment named `cinecontext`.

### Verify the result

1. Select **New research** and enter “Find five films inspired by Norse mythology and show how they relate to the mythological timeline.”
2. Review the brief, then start the research. The result should show **Completed** and five films. The release timeline shows entries for 1986, 1989, 2011, 2013, and 2022. The mythology timeline follows four stages from creation through renewal.
3. Select *Gåten Ragnarok* (2013). The detail panel shows the film information, its two release entries, sources, and demo rating snapshots. The guide cites sources included in the result.

### Enable live research with TACC

Configure the course `tcloud` SDK on the computer running the backend. Follow the [TACC setup](docs/deployment.md#tacc-qwen) to set the model mode and optional TMDB token in `.env`. Submit the first job and start the app from Bash. Once the app is tracking a job, use **Request TACC GPUs** in the sidebar to replace it when it ends.

```bash
CINECONTEXT_PYTHON="$PWD/.venv/bin/python" ./scripts/start.sh --submit-tacc
```

Wait for **TACC ready**. A single GPU job can serve multiple research requests until its time limit. An existing job can be passed to `start.sh` by job ID. The backend also accepts a configured [OpenAI-compatible endpoint](docs/deployment.md#deepseek--openai-compatible-example).

## Detailed Instructions

### Reproduce the software checks

Install the backend in editable mode if it has not been installed by the launcher. Run these commands in the repository root. Use the interpreter from your project environment.

```bash
python -m pip install -e backend
python -m unittest tests.test_ai_workflow
npm --prefix frontend ci
npm --prefix frontend run build
```

With the demo running, run the end-to-end checks in a second terminal.

```bash
python tests/smoke.py
```

Set `CINECONTEXT_TEST_URL` if you changed the web port. The checks create real research runs in the connected database and poll for their result. They use fixtures from `backend/cinecontext/mock/` and exercise four cases.

| Case | Expected result |
|---|---|
| Five films inspired by Norse mythology | Completed, with chronological release dates, four myth stages, film-specific citations, and a demo rating |
| European films on surveillance released in the 1980s | Partial, with no selection and an explanation of the release constraint |
| Three films about Romanian political memory | Completed, with an ordered historical timeline and two films linked to the 1989 event |
| Films about settling Mars | Partial, with no films in the demo corpus |

### Verify a live run

With TACC ready and TMDB configured, ask for four key films for a newcomer to the Marvel Cinematic Universe. The criteria should ask for four films. Check that the viewing order contains four distinct films when all four are supported, and open their posters to see the synopsis, rating source, and available reviews. BFI supplies editorial coverage for several MCU titles. Verified editorial coverage varies by film, and each article links to its publisher. OMDb coverage requires its own API key.

Live film selection, sources, and latency vary with the model, tool responses, and external service availability. The TACC queue wait is separate from the execution time of a research request. The deterministic demo covers three themes, with eleven films. Run logs are written to `.runtime/logs/cinecontext.log` by default. See [logs and diagnostics](docs/deployment.md#logs-and-diagnostics).

### Architecture and repository layout

```text
React -> FastAPI controllers -> application services -> domain ports
                                       |
                     +-----------------+------------------+
                     |                                    |
              AI workflow and providers        scoped repositories
              TACC / TMDB / web / OMDb          PostgreSQL / Alembic
```

| Path | Responsibility |
|---|---|
| `backend/alcedo/` | Vendored Alcedo framework for dependency injection, configuration, and units of work |
| `backend/cinecontext/api/`, `application/`, `domain/` | HTTP handlers, use cases and transactions, and domain data and protocols |
| `backend/cinecontext/ai/`, `infrastructure/` | Model and research adapters, workflow, validation, and SQLAlchemy persistence |
| `backend/cinecontext/mock/`, `backend/migrations/` | Deterministic demo and Alembic schema |
| `frontend/`, `scripts/`, `deploy/tacc/` | React app, local launchers, and the course GPU job bundle |
| `tests/`, `docs/` | Unit and end-to-end checks, and technical documentation |

Production and demo entry points share `backend/cinecontext/bootstrap.py`. Research executes in a single FastAPI process. The browser polls for progress, and a service restart marks any unfinished run as interrupted. Production guides are written in English, and source excerpts can retain their original language.

- [System architecture](docs/architecture.md)
- [Deployment and configuration](docs/deployment.md)
- [AI workflow](docs/ai-workflow.md)
- [Research tools and function calling](docs/function-calling.md)
- [Development and API guide](docs/development.md)
