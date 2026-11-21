# Research Tools and Function Calling

The application controls when tools run. The model returns a search plan, candidate assessments, timeline research, and guide drafts. Workflow nodes invoke typed research interfaces, record results, and select the next state. The model interface uses structured JSON responses. It supplies no SDK `tools` or `tool_choice` list to the model.

```text
WorkflowRunner -> node -> ResearchTools -> provider
                    <- typed result <-------
                    -> persist and update context -> TransitionEngine
```

This design bounds retries, preserves evidence provenance, and leaves provider selection in one place. External calls finish before their results enter a short database transaction. See the [workflow contracts](ai-workflow.md) and [system architecture](architecture.md).

## Tool interface

[research_tools/main.py](../backend/cinecontext/ai/research_tools/main.py) defines the `ResearchTools` protocol. Its facade delegates to local search, metadata, web, and rating providers. The factory chooses configured providers without changing the workflow.

| Method | Input and output | Stage and source |
|---|---|---|
| `find_candidates` | Theme `str` → candidate UUIDs | Discovery, local `films.facts` |
| `lookup_local_movie` | Film UUID → `FilmProjection` | Discovery, local films and releases |
| `lookup_movie` | `FilmHint` → `FilmProjection \| None` | Discovery, TMDB when configured |
| `search_passages` | Query and optional film UUID → evidence drafts | Discovery and follow-up, PostgreSQL full-text search |
| `search_web` | Query and optional film UUID → evidence drafts | Discovery and follow-up, selected web provider |
| `search_editorial` | Verified film and optional focus → editorial evidence drafts | Discovery and follow-up, curated readings and targeted publication search |
| `lookup_ratings` | `FilmProjection` → ratings | Ratings stage, OMDb when configured and IMDb ID is available |

TMDB verifies a single title and optional year match before persisting metadata, releases, and the audience rating. Passage search uses TSVECTOR, a GIN index, and `websearch_to_tsquery`. It targets film-specific passages when given a film UUID, or shared context when the film is omitted.

Web search uses Brave LLM Context when configured. The other defaults are Wikipedia for TACC and DuckDuckGo for the standard configuration. DuckDuckGo can use the crawler to extract text, with a search snippet fallback. The crawler follows public HTTP(S) locations, checks redirect targets and DNS, bounds the response and page counts, and reads supported content types without executing JavaScript. A transient upstream error can be retried within the configured time budget.

Rating lookup is ancillary. Optional OMDb returns IMDb, Rotten Tomatoes, and Metacritic values for the verified IMDb ID. TMDB scores arrive with metadata.

Editorial search checks film titles against their release year or director. It queries Senses of Cinema directly, follows publisher references from a matched Wikipedia film page, and tries the configured search providers. These paths support article discovery without a Brave key. An editorial passage must match the selected film before it enters the evidence bundle for Qwen. Qwen can request a focused follow-up when it needs more evidence and cites relevant critical observations in the guide.

The film dialog displays saved articles first and looks for missing publishers in the background. It labels catalogue entries, research sources, and online results, and displays short excerpts when available. When the app can confirm a Criterion reference but publisher access is blocked, the dialog displays the link with an access label. Only readable evidence is passed to Qwen.

## Model methods

| Method | Result |
|---|---|
| `parse` | Structured `Constraints` and a brief interpretation |
| `plan` | Search queries and candidate hints |
| `assess` | Candidate decisions with references to supplied evidence |
| `timeline` | Evidenced story, myth, or historical events |
| `generate` | A guide with claim-level source references |

The production adapter validates JSON from an OpenAI-compatible API. The demo supplies deterministic responses to the same interface.
For guide generation through the course TACC SDK, the adapter also requests a JSON schema with the exact number of selected films. The normal guide validation checks film coverage and citations before publication.

## Execution and audit

```text
PLAN          model.plan
DISCOVER      local catalog, TMDB, passage and web search
ASSESS        model.assess
FOLLOW_UP     targeted passage or web search, then reassessment
TIMELINE      model.timeline and catalog release dates
GUIDE         model.generate and source validation
RATINGS       optional OMDb
```

`WorkflowOperations.logged` records the operation name, status (`ok`, `empty`, or `failed`), elapsed milliseconds, and safe detail in `tool_calls`. Examples include `lookup_movie`, `search_passages.context`, `search_web.follow_up`, and `lookup_ratings`. Recoverable external errors add research limitations when appropriate. Unhandled core errors terminate the run.

The workflow persists `EvidenceDraft` objects as `run_evidence` before citing them. Each stored record has an internal UUID. Numbered references used by model drafts resolve only to records supplied by the application.

## Extending the tool interface

If native SDK function calling is added later, map a defined set of tool names to typed, validated commands. Apply permissions, request bounds, timeouts, and auditing to each call. Keep the crawler restrictions, database transaction boundaries, and workflow state rules. Persist retrieved evidence before model steps can reference it.
