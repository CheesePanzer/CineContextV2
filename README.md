# CineContext

CineContext turns a film research question into a source-backed viewing guide. It parses user criteria, resolves referenced works against public sources, retrieves candidate films from TMDB, Wikipedia, and film criticism publishers (Criterion, BFI / Sight and Sound, Senses of Cinema, The Guardian, BBC Culture, Film Comment, Variety, and RogerEbert.com), verifies each candidate against the brief, and organizes the final lineup across viewing order, release, story, mythology, and historical timelines.

The repository includes a deterministic **demo mode** (no credentials required) and a **live TACC mode** (2×GPU Qwen 27B INT4 via vLLM). Both modes share the same FastAPI server, PostgreSQL schema, Alembic migrations, React UI, and research workflow.

---

## Getting Started Instructions

### Prerequisites

- **Python**: `3.14` (`python3.14 --version`)
- **Node.js & npm**: Node `22.12+` (`node -v`, `npm -v`)
- **PostgreSQL**: `initdb` and `pg_ctl` on `PATH` for automatic local database initialization, or an existing PostgreSQL instance via `DATABASE_URL`
- **Live TACC mode only**: the course `tcloud` CLI authenticated for the `tacc` partition (single node, `gres=gpu:2`) and a TMDB read access token (`APP__TOOLS__TMDB__ACCESS_TOKEN`)

### Quickstart for Human Reviewers and Automated Agents

Every verification flow below is non-interactive and scriptable. Use a clean checkout (or unset/remove any local `.env` that selects `production`/`tacc`) when running Demo mode.

#### Option A: Start and verify Demo mode (no API keys or TACC required)

```bash
# 1. Create environment, install editable backend, run 75 unit tests, build frontend
python3.14 -m venv .venv
.venv/bin/python -m pip install -e backend
PYTHONPATH=backend .venv/bin/python -m unittest tests.test_ai_workflow
npm --prefix frontend ci --no-audit --no-fund
npm --prefix frontend run build

# 2. Start the demo server on http://127.0.0.1:8765 (initializes local PostgreSQL automatically)
CINECONTEXT_PYTHON="$PWD/.venv/bin/python" ./scripts/start.sh
```

On Windows PowerShell:

```powershell
py -3.14 -m venv .venv
.\scripts\start.ps1
# If execution policy blocks the script:
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

In a second terminal, wait for `/api/health` to report `{"status":"ok","model_mode":"mock"}` and run the 4-case end-to-end smoke suite:

```bash
curl -sf http://127.0.0.1:8765/api/health
.venv/bin/python -m unittest -v tests.smoke
```

Expected exit code: `0` (`Ran 4 tests ... OK`, completing in ~1 second).

#### Manual UI check (small end-to-end example)

1. Open `http://127.0.0.1:8765`, select **New research**, and enter:
   `I'm interested in Italian neorealism. Which films should I watch?`
2. Confirm the parsed criteria and start the research.
3. Expected output:
   - Opens on **Viewing order** with status **Completed** and 3 films: *Rome, Open City* (1945), *Bicycle Thieves* (1948), and *Umberto D.* (1952).
   - Switching to **Release date** orders the three films chronologically (`1945`, `1948`, `1952`).
   - Expanding **Recommendation sources** under *Bicycle Thieves* shows its cited editorial article. Clicking the poster opens the detail dialog with the TMDB audience score snapshot (`8.16 / 10`) and saved criticism from Criterion, BFI, and Senses of Cinema.

#### Option B: Deploy and verify Live TACC mode

Create `.env` in the repository root (see `.env.example` and [docs/deployment.md](docs/deployment.md#tacc-qwen)):

```env
CINECONTEXT_APP=production
APP_ENVIRONMENT=tacc
CINECONTEXT_PORT=8765
CINECONTEXT_DB_PORT=55438
APP__MODEL__TCLOUD_BIN=/absolute/path/to/tcloud
APP__TOOLS__TMDB__ACCESS_TOKEN=<YOUR_TMDB_READ_ACCESS_TOKEN>
```

Submit a 1-hour 2×GPU TACC job and start the local server:

```bash
CINECONTEXT_PYTHON="$PWD/.venv/bin/python" ./scripts/start.sh --submit-tacc
```

Poll `GET http://127.0.0.1:8765/api/health` until `"model_status": "ready"` (typically 3–6 minutes after a GPU node is allocated). You can also attach to an existing job via `./scripts/start.sh <JOB_ID>`, or trigger replacements from the UI (**Request TACC GPUs** / **Restart · 1 hour**) or `POST /api/tacc/jobs`. If a TACC job exits during startup because an allocated node lacks free GPU memory (`< 13,500 MiB` per GPU), the backend reads its Slurm log and automatically excludes that node for 30 minutes on the next submission. Any [OpenAI-compatible endpoint](docs/deployment.md#deepseek--openai-compatible-example) is also supported.

---

## Detailed Instructions

### 1. Automated software checks (unit, frontend build, and demo end-to-end)

| Check | Command | Expected outcome |
|---|---|---|
| Backend unit tests | `PYTHONPATH=backend .venv/bin/python -m unittest tests.test_ai_workflow` | `Ran 75 tests ... OK` (covers request parsing, reference-work resolution, sequel prerequisite handling, TMDB title/duplicate disambiguation, workflow state transitions, evidence/citation validation, 16K context budgeting, multi-publisher editorial search, timeline ordering, and TACC job lifecycle / unhealthy-node cooldown) |
| Frontend clean build | `npm --prefix frontend ci && npm --prefix frontend run build` | Clean Vite production bundle in `frontend/dist/` |
| Demo end-to-end API & DB check | `CINECONTEXT_TEST_URL=http://127.0.0.1:8765 .venv/bin/python -m unittest -v tests.smoke` | `Ran 4 tests ... OK` (creates real PostgreSQL runs, polls to completion, verifies viewing order, timeline sorting, editorial citations in reasons, and four-source coverage) |

The four canonical prompts tested by both `tests/smoke.py` (deterministic fixtures in `backend/cinecontext/mock/`) and `tests/eval_live.py` (live TACC Qwen 27B) correspond to the functional and live evaluation tables in our project report:

| Case key | Prompt | Expected demo result (`tests/smoke.py`) |
|---|---|---|
| `medieval` | `I'm interested in Kingdom Come: Deliverance II. Can you recommend a few films about medieval European history?` | *Medieval* (2022), *The Passion of Joan of Arc* (1928), and *Marketa Lazarová* (1967); `setting_match=nearby`; story timeline includes `1402, Bohemia`. |
| `romanian` | `I'm interested in the Romanian New Wave. Which films should I watch?` | *4 Months, 3 Weeks and 2 Days* (2007), *Tuesday, After Christmas* (2010), and *Child's Pose* (2013); *4 Months, 3 Weeks and 2 Days* verifies TMDB score + Criterion, BFI, and Senses of Cinema. |
| `italian` | `I'm interested in Italian neorealism. Which films should I watch?` | *Rome, Open City* (1945), *Bicycle Thieves* (1948), and *Umberto D.* (1952); *Bicycle Thieves* verifies TMDB score + Criterion, BFI, and Senses of Cinema. |
| `spiderman` | `I'm getting ready to watch Spider-Man: Brand New Day. Please recommend four films to watch beforehand to catch up on the necessary background and story.` | *Spider-Man: Homecoming* (2017), *Avengers: Endgame* (2019), *Spider-Man: Far From Home* (2019), and *Spider-Man: No Way Home* (2021) in chronological viewing order; 4 story-timeline events; BFI / Sight and Sound citations on all four films. |

### 2. Automated live TACC evaluation (`tests/eval_live.py`)

With the live server running (`http://127.0.0.1:8765`, or pass `--url`), run the zero-dependency live evaluation script:

```bash
.venv/bin/python -u tests/eval_live.py \
  --url http://127.0.0.1:8765 \
  --auto-resubmit \
  --output eval_live_results.json
```

Optional subset execution (e.g., to test a single case quickly):

```bash
.venv/bin/python -u tests/eval_live.py --url http://127.0.0.1:8765 --cases italian
```

- **Cluster & model configuration** (`deploy/tacc/tuxiv.conf` and `deploy/tacc/job.sh`): TACC `tacc` partition, 1 node, `gres=gpu:2`, `time=01:00:00`, Qwen 27B INT4 served via vLLM tensor parallelism (`--max-model-len 16384`, `--gpu-memory-utilization 0.55`, `--max-num-seqs 1 --enforce-eager`), supervised by `deploy/tacc/service_supervisor.py`.
- **Automatic job management**: `--auto-resubmit` waits for `"model_status": "ready"` and automatically requests a fresh 1-hour TACC job if the tracked job stops or has less than 750 seconds remaining before starting the next case.
- **Reference live outcomes** (measured on TACC Job `30460`, `qwen3.8-27b`, 2026-11-22, all 4 cases completing in ~37.5 minutes total inside one 1-hour job):

| Case | Status | Latency | Live viewing order | Timeline views | Editorial in reason | TMDB ratings | Saved criticism publishers |
|---|---|---|---|---|---|---|---|
| `medieval` | `completed` (3/3) | 688s | *Medieval* (2022) → *The Passion of Joan of Arc* (1928) → *Marketa Lazarová* (1967) | `release`, `story`, `history` | 3/3 | 3/3 | BFI / Sight and Sound, Criterion, Senses of Cinema, The Guardian, BBC Culture, Film Comment, Czech National Film Archive |
| `romanian` | `completed` (3/3) | 486s | *4 Months, 3 Weeks and 2 Days* (2007) → *Child's Pose* (2013) → *Tuesday, After Christmas* (2010) | `release`, `story`, `history` | 3/3 | 3/3 | Criterion, BFI / Sight and Sound, Senses of Cinema, Film Comment |
| `italian` | `completed` (3/3) | 497s | *Rome, Open City* (1945) → *Bicycle Thieves* (1948) → *Umberto D.* (1952) | `release`, `story` | 3/3 | 3/3 | Criterion, BFI, Senses of Cinema |
| `spiderman` | `completed` (4/4) | 585s | *Spider-Man: Homecoming* (2017) → *Avengers: Endgame* (2019) → *Spider-Man: Far From Home* (2019) → *Spider-Man: No Way Home* (2021) | `release` (plus `story` on clean DB) | 4/4 | 4/4 | BFI / Sight and Sound, Film Comment, Senses of Cinema |

- **Verify TACC GPU memory cleanup**: Cancel the active job (`tcloud cancel -j <JOB_ID>`) and inspect `tcloud cat slurm_log/cinecontext-qwen-<JOB_ID>.log`. Expected output includes `CINECONTEXT_CLEANUP` followed by `GPU memory after service shutdown (MiB)` confirming both GPUs returned to full free memory.
- **Runtime logs**: Written to `.runtime/logs/cinecontext.log` (see [logs and diagnostics](docs/deployment.md#logs-and-diagnostics)).

### 3. API contract and environment variables (for agent integration)

| Endpoint | Method | Purpose / Expected JSON |
|---|---|---|
| `/api/health` | `GET` | Health & readiness (`status`, `model_mode`, `model_status`: `ready`/`starting`/`queued`/`stopped`/`unavailable`, `tacc_job.id`, `tacc_job.remaining_seconds`) |
| `/api/tacc/jobs` | `POST` | Submit/restart TACC job: `{"previous_job_id": "<id>", "restart": false}` → `{"job_id": "<new_id>"}` |
| `/api/requests/parse` | `POST` | Parse prompt into structured criteria: `{"prompt": "..."}` → `{"constraints": {...}, "reference_match": ..., "clarification": "..."}` |
| `/api/runs` | `POST` | Start curation run: `{"constraints": {...}, "submission_key": "<uuid>"}` → `{"run_id": "<uuid>"}` |
| `/api/runs/{run_id}` | `GET` | Poll status (`queued`/`running`/`completed`/`partial`/`failed`/`interrupted`), `stage`, `study`, and `guide` |
| `/api/runs/{run_id}/films/{film_id}/readings` | `GET` | Return saved criticism articles for a film in a run |
| `/api/films/{film_id}/ratings` | `GET` | Return persisted TMDB / OMDb rating snapshots |

Key environment variables:
- `CINECONTEXT_APP`: `mock` (default) or `production`
- `APP_ENVIRONMENT`: `mock` (default) or `tacc`
- `CINECONTEXT_PORT` / `CINECONTEXT_DB_PORT`: web port (default `8765`) and local PostgreSQL port (default `55438`)
- `DATABASE_URL`: optional external PostgreSQL asyncpg URL (skips local `initdb`/`pg_ctl`)
- `CINECONTEXT_TEST_URL`: base URL targeted by `tests/smoke.py` and `tests/eval_live.py` (default `http://127.0.0.1:8765`)
- `APP__MODEL__TCLOUD_BIN`, `APP__TOOLS__TMDB__ACCESS_TOKEN`, `APP__TOOLS__OMDB__API_KEY`: TACC CLI path and optional metadata/rating keys

### 4. Known limitations and expected variation

- **Non-deterministic live outputs**: Film choices in open-ended historical queries, phrasing of notes, and per-run execution time (~7–12 minutes per case on 2×GPU Qwen 27B INT4) vary across runs due to LLM sampling and live web/API responses. Reproducible findings are completion rate, source-grounded editorial citations, timeline construction, and rejection of out-of-scope or target-sequel candidates.
- **Strict verification and `partial` results**: If a 27B model hallucinates a wrong release year or proposes out-of-range titles (e.g., on narrow regional/decade queries), TMDB year verification and `AssessNode` intentionally reject them and return `partial` (amber status indicator) rather than fabricating unverified films.
- **TACC queue and node health**: Slurm queue wait is separate from execution time, and each job has a 1-hour limit. If an allocated node has leftover GPU memory usage, `job.sh` exits on its free-memory check (`>= 13,500 MiB` per GPU) and `TaccJobs` automatically excludes that node for 30 minutes on the next submission.
- **External publisher and rating coverage**: Automated full-text fetches to Criterion or RogerEbert.com may receive HTTP `403`; when full text cannot be verified, links are either recorded as external references without unverified excerpts or supplemented by open-access publishers (BFI / Sight and Sound, Senses of Cinema, The Guardian, BBC Culture, Film Comment, Variety). OMDb critic scores require a separate `APP__TOOLS__OMDB__API_KEY`.
- **Context window and language**: Evidence bodies passed to `assess`, `timeline`, and `generate` are dynamically compacted to stay safely within vLLM's 16,384-token window while preserving verbatim prefix quotes for setting checks. Production viewing guides are written in English.

### 5. Architecture and codebase reading map

```text
React -> FastAPI controllers -> application services -> domain ports
                                       |
                     +-----------------+------------------+
                     |                                    |
              AI workflow and providers        scoped repositories
              TACC / TMDB / web / OMDb          PostgreSQL / Alembic
```

Key files to inspect first (in reading order):
1. `scripts/start.sh` — single entry point for DB init, Alembic migrations, frontend build, TACC submit, and Uvicorn startup
2. `backend/cinecontext/bootstrap.py` — shared Alcedo DI container wiring for both demo and production apps
3. `backend/cinecontext/api/controllers.py` & `application/services.py` — HTTP routes, request parsing, reference-work lookup, and run lifecycle
4. `backend/cinecontext/ai/nodes.py` & `openai_model.py` — state-machine workflow (`Plan -> Discover -> Assess -> FollowUp -> Select -> Timeline -> Guide -> Ratings -> Publish`) and structured prompts with 16K context budgeting
5. `backend/cinecontext/ai/research_tools/` (`tmdb.py`, `wikipedia.py`, `editorial.py`) — TMDB lookup/disambiguation, Wikipedia extracts & external links, and 8-publisher criticism retrieval
6. `backend/cinecontext/ai/tacc_transport.py` & `infrastructure/tacc_jobs.py` — TACC SDK transport, readiness checks, and automatic 30-minute unhealthy-node cooldown
7. `deploy/tacc/` (`tuxiv.conf`, `job.sh`, `service_supervisor.py`, `worker.py`) — Slurm job bundle and process-group GPU cleanup supervisor
8. `frontend/src/components/Results.jsx` — Viewing order default view, timeline switcher, recommendation sources, and film detail modal
9. `tests/test_ai_workflow.py`, `tests/smoke.py`, `tests/eval_live.py` — unit, demo end-to-end, and live TACC evaluation suites

Technical references:
- [System architecture](docs/architecture.md)
- [Deployment and configuration](docs/deployment.md)
- [AI workflow](docs/ai-workflow.md)
- [Research tools and function calling](docs/function-calling.md)
- [Development and API guide](docs/development.md)
