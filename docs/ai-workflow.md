# AI Workflow

See the [system architecture](architecture.md) for application layers and transaction boundaries.

## State graph

```text
PLAN --success--> DISCOVER --success--> ASSESS
ASSESS --follow_up--> FOLLOW_UP --success--> ASSESS
ASSESS --success--> SELECT
SELECT --success--> TIMELINE --success--> GUIDE
SELECT --partial--> PUBLISH_PARTIAL --success--> COMPLETED
GUIDE --retry--> GUIDE
GUIDE --success/partial--> RATINGS
RATINGS --success--> PUBLISH --success--> COMPLETED
RATINGS --partial--> PUBLISH_PARTIAL --success--> COMPLETED
Uncaught exception --> CurationService --> persist failed run
```

`FAILED` is a terminal state type. `CurationService` records it when an unhandled exception leaves the runner.

## Workflow contracts

[workflow.py](../backend/cinecontext/ai/workflow.py) defines the orchestration primitives.

| Type | Contract |
|---|---|
| `WorkflowContext` | One mutable context per run. Nodes update it in place. |
| `WorkflowNode` | Implements `async run(ctx) -> ctx`. |
| `CurationState` | Identifies a node or one of the terminal states. |
| `NodeOutcome` | `SUCCESS`, `FOLLOW_UP`, `RETRY`, or `PARTIAL`. |
| `WorkflowTransition` | Immutable rule from a source and outcome to a target, with an optional guard. |
| `TransitionEngine` | Resolves exactly one rule for the current state and outcome. |
| `WorkflowRunner` | Clears the previous outcome, runs the node, checks its contract, and performs the transition. |

Zero matching rules and ambiguous rules both raise `WorkflowTransitionError`. The runner also rejects unregistered states, a missing outcome, a replaced context, or an exceeded step budget. Criteria, plan, and guide are defined in [contracts.py](../backend/cinecontext/contracts.py). Films, evidence, decisions, studies, and timelines use types from [ai/types.py](../backend/cinecontext/ai/types.py). Stable enum values appear in API data and tool logs. Methods such as `require_plan()`, `require_study()`, and `require_guide()` report missing prerequisites explicitly.

## Nodes and budgets

| Node | Responsibility |
|---|---|
| `PlanNode` | Ask the model for search queries and candidate hints. |
| `DiscoverNode` | Search the catalog and TMDB as configured, and gather film-specific and general evidence. |
| `AssessNode` | Get structured decisions, check evidence ownership and user constraints, and identify gaps. |
| `FollowUpNode` | Retrieve more evidence for one film and reassess only that film. |
| `SelectNode` | Prioritize primary matches, honor the requested count, and save the study. An empty selection produces a partial result. |
| `TimelineNode` | Research story, myth, or history and add catalog release dates. |
| `GuideNode` | Generate a guide and validate film coverage and citations. |
| `RatingsNode` | Fetch optional OMDb ratings concurrently after selection. |
| `PublishNode` and `PublishPartialNode` | Publish the guide or available study as completed or partial. |

Node instances hold no run state. Each workflow currently creates a new node collection. Follow-up needs a unique, non-empty query on a film marked `needs_evidence` when more accepted films are required. A search with no new evidence skips reassessment.

| Limit | Value |
|---|---:|
| `MAX_FOLLOW_UP_QUERIES` | 4 distinct film-and-query pairs per run |
| `MAX_FOLLOW_UPS_PER_FILM` | 2 |
| `MAX_GUIDE_ATTEMPTS` | 2, including the first attempt |
| `MAX_WORKFLOW_STEPS` | 32 |

## Timeline and guide validation

[timeline.py](../backend/cinecontext/ai/timeline.py) builds release events deterministically from catalog records. Model-produced story, myth, and historical events must cite stored evidence and identify only selected films. Duplicate event IDs and invalid references are discarded. Historical events require a sort year and use available month or day information for chronological ordering. Myth events require a version and use relative order without calendar years. Order edges must connect existing distinct events with the same version and valid evidence. Unresolved cycles are excluded from views that use topological order.

The model receives small numbered references instead of raw UUIDs for assessment and guide generation. The adapter resolves these numbers against the supplied films and sources. An invalid assessment reference cannot count as support for a film. A decision that lacks usable evidence can enter follow-up.

[guide_validation.py](../backend/cinecontext/ai/guide_validation.py) checks that `film_guides` and `viewing_order` each cover every selected film exactly once. Each claim must cite published evidence. A film-specific claim can cite evidence for that film or shared evidence with `film_id=None`. The first validation failure supplies up to 600 characters of feedback for one revision. If the second attempt fails, the workflow continues to ratings and publishes the study as partial.

## Auditing and failures

Nodes call [research tools](function-calling.md) through typed ports. `WorkflowOperations.logged` writes the `OperationName`, elapsed milliseconds, outcome, and safe error detail to `tool_calls`. Persistence uses short Alcedo scopes. Evidence is deduplicated by film, source URL, and text, then stored before the model can cite it.

DEBUG records node starts, transitions, and durations. INFO records terminal outcomes. Warnings describe recoverable failures and structured retries. `CurationService` writes a full stack once for an unhandled failure, keyed by a diagnostic UUID. See [deployment diagnostics](deployment.md#logs-and-diagnostics) for safe troubleshooting.

Optional metadata, web, or rating failures can add a limitation and allow research to continue. A transient search failure can receive a bounded retry. A timeline model failure keeps the release view. A guide provider failure sends the study to partial publication. Core failures in planning or assessment fail the run. A malformed model response normally receives one structured retry before the relevant failure path takes over.

## Retry a guide

`POST /api/runs/{run_id}/retry-guide` claims an eligible terminal run under a row lock and restores the saved criteria, selected films, and evidence. It updates the run to `running/generating` and clears the old guide and error.

```text
GUIDE --retry--> GUIDE
GUIDE --success--> PUBLISH --> COMPLETED
GUIDE --partial--> PUBLISH_PARTIAL --> COMPLETED
```

The saved timeline, candidate decisions, and evidence are reused. Earlier research steps and rating lookup are outside the retry graph.

## Extend the workflow

For a new node, register a typed state and every possible transition, then check the full step budget. Run external operations through the logged or optional wrapper. Keep state in the context, and keep network calls outside database transactions.

For a model provider, implement `ApplicationModel` and its factory with a client that can be closed per run. Validate structured responses in the adapter, treat prompt and retrieved text as data, and verify error mapping in the parsing and research services.

For a research provider, implement a protocol from `research_tools/main.py` in a dedicated module and select it in `create_research_tools`. Return typed projections, evidence, or ratings. Test provider selection, empty and failed responses, parsing, and persistence. Preserve the crawler’s public URL, redirect, size, and content-type restrictions.
