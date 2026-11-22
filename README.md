# CineContext

CineContext turns a film research question into a source-backed viewing guide. It parses user criteria, resolves referenced works against public sources, retrieves candidate films from TMDB, Wikipedia, and film criticism publishers (Criterion, BFI / Sight and Sound, Senses of Cinema, The Guardian, BBC Culture, Film Comment, Variety, and RogerEbert.com), verifies each candidate against the brief, and organizes the final lineup across viewing order, release, story, mythology, and historical timelines.

The repository includes a deterministic **demo mode** (no credentials required) and a **live TACC mode** (2×GPU Qwen 27B INT4 via vLLM). Both modes run the same FastAPI server, PostgreSQL schema, Alembic migrations, React UI, and research workflow.

## Getting Started Instructions

### Prerequisites

- Python 3.14, Node.js 22.12+, `npm`, and PostgreSQL (`initdb` and `pg_ctl` on `PATH` for automatic local DB setup, or an existing PostgreSQL instance via `DATABASE_URL=postgresql+asyncpg://user:password@host:5432/dbname`).
- For live TACC mode only: the course `tcloud` CLI authenticated for the `tacc` partition (1 node, `gres=gpu:2`) and a TMDB v4 Read Access Token (`APP__TOOLS__TMDB__ACCESS_TOKEN`).

### Start the demo (no API keys or TACC required)

Use a clean checkout (or remove/override any local `.env` selecting `production`/`tacc`). On macOS or Linux:

```bash
git clone https://github.com/CheesePanzer/CineContextV2.git && cd CineContextV2
python3.14 -m venv .venv
CINECONTEXT_PYTHON="$PWD/.venv/bin/python" ./scripts/start.sh
```

On Windows PowerShell (use `powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1` if execution policy blocks the script):

```powershell
py -3.14 -m venv .venv
.\scripts\start.ps1
```

The launcher installs backend dependencies, initializes and migrates PostgreSQL, builds the frontend (`npm ci && npm run build`), and runs Uvicorn in the foreground at `http://127.0.0.1:8765`. Start it in a background process or separate terminal and verify readiness non-interactively via `curl -sf http://127.0.0.1:8765/api/health`, which returns `{"status":"ok","model_mode":"mock",...}`.

### Small end-to-end verification example

1. Open `http://127.0.0.1:8765`, select **New research**, and enter:
   `I'm interested in Italian neorealism. Which films should I watch?`
2. Review the parsed criteria and start the research.
3. Expected output:
   - Opens on **Viewing order** with status **Completed** and 3 films: *Rome, Open City* (1945), *Bicycle Thieves* (1948), and *Umberto D.* (1952).
   - Switching to **Release date** orders them chronologically (`1945`, `1948`, `1952`).
   - Expanding **Recommendation sources** under *Bicycle Thieves* shows its cited editorial article; clicking its poster opens the detail dialog with the TMDB audience score (`8.16 / 10`) and saved criticism from Criterion, BFI, and Senses of Cinema.

### Enable live research with TACC

Create `.env` from `.env.example` (omit `APP__MODEL__TCLOUD_BIN` if `tcloud` is already on `PATH`; see [docs/deployment.md](docs/deployment.md#tacc-qwen)):

```env
CINECONTEXT_APP=production
APP_ENVIRONMENT=tacc
CINECONTEXT_PORT=8765
CINECONTEXT_DB_PORT=55438
APP__MODEL__TCLOUD_BIN=/absolute/path/to/tcloud
APP__TOOLS__TMDB__ACCESS_TOKEN=<YOUR_TMDB_READ_ACCESS_TOKEN>
```

Submit a 1-hour 2×GPU job and start the app (or pass an existing `<JOB_ID>` instead of `--submit-tacc`):

```bash
CINECONTEXT_PYTHON="$PWD/.venv/bin/python" ./scripts/start.sh --submit-tacc
```

Poll `GET http://127.0.0.1:8765/api/health` until `"model_status": "ready"` (typically 3–6 minutes after node allocation). Replacements can be triggered from the sidebar (**Request TACC GPUs** / **Restart · 1 hour**) or `POST /api/tacc/jobs`. If a job exits during startup due to insufficient free GPU memory (`< 13,500 MiB` per GPU), the backend parses its Slurm log and temporarily excludes that node for 30 minutes on the next submission. Any [OpenAI-compatible endpoint](docs/deployment.md#deepseek--openai-compatible-example) is also supported.

## Detailed Instructions

### 1. Automated unit, build, and demo end-to-end checks

Run from the repository root using the project environment:

```bash
.venv/bin/python -m pip install -e backend
PYTHONPATH=backend .venv/bin/python -m unittest tests.test_ai_workflow
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run build
```

Expected output: `Ran 75 tests ... OK` (request parsing, reference resolution, sequel prerequisite handling, TMDB disambiguation, state transitions, citation validation, 16K context budgeting, 8-publisher editorial retrieval, timeline ordering, and TACC job/cooldown control) and a clean Vite build in `frontend/dist/`.

With the demo server running (`http://127.0.0.1:8765`, overridable via `CINECONTEXT_TEST_URL`), run the 4-case end-to-end smoke suite in a second terminal:

```bash
.venv/bin/python -m unittest -v tests.smoke
```

Expected output: `Ran 4 tests ... OK` (~1s). Each test creates a real PostgreSQL run via the HTTP API, polls to completion, and checks viewing order, timeline sorting, editorial citations in recommendation reasons, and four-source coverage against fixtures in `backend/cinecontext/mock/`. These four prompts match the functional and live evaluation tables in our project report:

| Case | Prompt | Expected demo result (`tests/smoke.py`) |
|---|---|---|
| `medieval` | `I'm interested in Kingdom Come: Deliverance II. Can you recommend a few films about medieval European history?` | *Medieval* (2022), *The Passion of Joan of Arc* (1928), *Marketa Lazarová* (1967); `setting_match=nearby`; story timeline includes `1402, Bohemia`. |
| `romanian` | `I'm interested in the Romanian New Wave. Which films should I watch?` | *4 Months, 3 Weeks and 2 Days* (2007), *Tuesday, After Christmas* (2010), *Child's Pose* (2013); 4-source check on *4 Months, 3 Weeks and 2 Days*. |
| `italian` | `I'm interested in Italian neorealism. Which films should I watch?` | *Rome, Open City* (1945), *Bicycle Thieves* (1948), *Umberto D.* (1952); 4-source check on *Bicycle Thieves*. |
| `spiderman` | `I'm getting ready to watch Spider-Man: Brand New Day. Please recommend four films to watch beforehand to catch up on the necessary background and story.` | *Spider-Man: Homecoming* (2017), *Avengers: Endgame* (2019), *Spider-Man: Far From Home* (2019), *Spider-Man: No Way Home* (2021); 4 story events; 4/4 BFI citations. |

### 2. Automated live TACC evaluation (`tests/eval_live.py`)

With the live TACC server running, execute the zero-dependency evaluation script (pass `--cases italian` to test a single case, or omit `--cases` to run all four):

```bash
.venv/bin/python -u tests/eval_live.py \
  --url http://127.0.0.1:8765 \
  --auto-resubmit \
  --output eval_live_results.json
```

- **Configuration** (`deploy/tacc/tuxiv.conf`, `deploy/tacc/job.sh`): `tacc` partition, 1 node, `gres=gpu:2`, `time=01:00:00`, Qwen 27B INT4 via vLLM tensor parallelism (`--max-model-len 16384`, `--gpu-memory-utilization 0.55`, `--max-num-seqs 1 --enforce-eager`), supervised by `deploy/tacc/service_supervisor.py`. `--auto-resubmit` waits for `"model_status": "ready"` and requests a replacement job if stopped or if under 750 seconds remain.
- **Reference live outcomes** (TACC Job `30460`, `qwen3.8-27b`, 2026-11-22; ~37.5 min total within one 1-hour job):

| Case | Status | Time | Live viewing order | Timelines | Editorial in reason | TMDB | Saved criticism publishers |
|---|---|---|---|---|---|---|---|
| `medieval` | `completed` (3/3) | 688s | *Medieval* (2022) → *The Passion of Joan of Arc* (1928) → *Marketa Lazarová* (1967) | release, story, history | 3/3 | 3/3 | BFI / Sight and Sound, Criterion, Senses of Cinema, The Guardian, BBC Culture, Film Comment, Czech National Film Archive |
| `romanian` | `completed` (3/3) | 486s | *4 Months, 3 Weeks and 2 Days* (2007) → *Child's Pose* (2013) → *Tuesday, After Christmas* (2010) | release, story, history | 3/3 | 3/3 | Criterion, BFI / Sight and Sound, Senses of Cinema, Film Comment |
| `italian` | `completed` (3/3) | 497s | *Rome, Open City* (1945) → *Bicycle Thieves* (1948) → *Umberto D.* (1952) | release, story | 3/3 | 3/3 | Criterion, BFI, Senses of Cinema |
| `spiderman` | `completed` (4/4) | 585s | *Spider-Man: Homecoming* (2017) → *Avengers: Endgame* (2019) → *Spider-Man: Far From Home* (2019) → *Spider-Man: No Way Home* (2021) | release (and story on clean DB) | 4/4 | 4/4 | BFI / Sight and Sound, Film Comment, Senses of Cinema |

- **GPU cleanup verification**: Run `tcloud cancel -j <JOB_ID>` and inspect `tcloud cat slurm_log/cinecontext-qwen-<JOB_ID>.log`. Expect `CINECONTEXT_CLEANUP` followed by `GPU memory after service shutdown (MiB)` showing full free memory restored on both GPUs. Runtime logs are written to `.runtime/logs/cinecontext.log`.

### 3. Known limitations and expected variation

- **Non-deterministic live runs**: Exact film selections in open-ended queries, prose phrasing, and latency (~7–12 min per case on 2×GPU Qwen 27B INT4) vary across runs. Completion rate, citation grounding, timeline construction, and exclusion of out-of-scope or target-sequel films are the reproducible properties.
- **Strict verification and `partial` results**: When the LLM proposes an out-of-range title or wrong release year, TMDB verification and `AssessNode` intentionally reject it and return `partial` (amber indicator) rather than fabricating unverified entries.
- **TACC queue and node health**: Queue wait time is separate from execution time, and jobs are capped at 1 hour. Nodes failing the startup free-memory check (`>= 13,500 MiB`/GPU) are automatically excluded for 30 minutes.
- **External publisher and rating access**: Automated fetches to Criterion or RogerEbert.com may receive HTTP `403`; unverified full texts are never fabricated and are supplemented by open-access publishers (BFI, Senses of Cinema, The Guardian, BBC Culture, Film Comment, Variety). OMDb critic scores require an optional `APP__TOOLS__OMDB__API_KEY`.
- **Context budget and language**: Evidence bodies passed to the model are dynamically compacted for vLLM's 16,384-token window while preserving verbatim prefix quotes. Production guides are generated in English.

### 4. Architecture, API, and codebase layout

```text
React -> FastAPI controllers -> application services -> domain ports
                                       |
                     +-----------------+------------------+
                     |                                    |
              AI workflow and providers        scoped repositories
              TACC / TMDB / web / OMDb          PostgreSQL / Alembic
```

Key HTTP endpoints (full API in [docs/development.md](docs/development.md)): `GET /api/health` (readiness & `tacc_job`), `POST /api/tacc/jobs`, `POST /api/requests/parse`, `POST /api/runs`, `GET /api/runs/{run_id}`, `GET /api/runs/{run_id}/films/{film_id}/readings`, and `GET /api/films/{film_id}/ratings`.

| Path | Responsibility |
|---|---|
| `scripts/start.sh`, `deploy/tacc/` | Launchers (DB, Alembic, frontend build, TACC submit) and 2×GPU Slurm job bundle with `service_supervisor.py` |
| `backend/alcedo/`, `backend/cinecontext/bootstrap.py` | Vendored Alcedo DI/UoW framework and shared container wiring for demo and production |
| `backend/cinecontext/api/`, `application/`, `domain/` | FastAPI controllers, request parsing, use cases/transactions, and domain entities |
| `backend/cinecontext/ai/`, `infrastructure/` | Workflow nodes (`nodes.py`), prompts/16K compaction (`openai_model.py`), tools (`tmdb.py`, `wikipedia.py`, `editorial.py`), TACC transport & auto-cooldown (`tacc_jobs.py`), SQLAlchemy persistence |
| `backend/cinecontext/mock/`, `backend/migrations/` | Deterministic demo fixtures and Alembic schema migrations |
| `frontend/src/components/Results.jsx`, `tests/` | React results UI (`Viewing order` default, timelines, film modal) and test suites (`test_ai_workflow.py`, `smoke.py`, `eval_live.py`) |

Further documentation: [System architecture](docs/architecture.md) · [Deployment and configuration](docs/deployment.md) · [AI workflow](docs/ai-workflow.md) · [Research tools](docs/function-calling.md) · [Development and API guide](docs/development.md).
