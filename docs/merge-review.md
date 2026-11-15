# Develop integration review

## Baseline and recovery

Reviewed on 15 November 2026. The integration starts from local `develop` at
`33128dc79b0aaafa8cf3ff9f8cc0dc4b81c3b8b7` and updates it to fetched
`origin/develop` at `0f9be24c983c97a056293b129e0f46cea1655a95`.

Before changing the base, the existing work passed 55 unit tests, the frontend
production build, and `git diff --check`. Tracked changes and 13 untracked source
files were saved in stash `edbd21d65a1da88c3adb8df40fd9ac62dd1630ac`.
That backup is retained. All 13 source files are present after integration.
Ignored credentials, runtime files, installed packages, and build artifacts were
excluded from the backup and remain local.

## Review findings and resolutions

| Finding | Resolution |
|---|---|
| The upstream commit moves the guide retry DTO out of the workflow module. Our application imports still used the old module. | Both the application service and AI curation service now import `GuideRetrySnapshot` from `ai/resume.py`. The upstream extraction is preserved. |
| Upstream accepts shared non-metadata evidence during assessment, while our guide draft resolver required film-owned evidence. This could accept a film during study and reject the same source during generation. | Guide generation accepts a shared non-metadata source that supports the film's relevance. The model instructions use the same rule. Metadata-only reasons and another film's private evidence remain invalid. |
| Upstream simplifies follow-up queries when cited evidence is missing. | Its query construction and shared-source assessment tests are retained. Our exact-year safeguards and preference ranking remain in place. An explicit year still requires a quoted film-owned source for strict matching. |
| The film-readings use case could retain a database read session during external search. | It now loads and checks the film in a short Alcedo scope, closes that scope, then calls the external reading adapter. A regression test also checks that an out-of-run film never triggers external search. |
| Restart controls depend on production service registration and HTTP request boundaries. | Added a production composition test covering health, same-origin local submission, cross-origin rejection, and remote-client rejection. All SDK actions are mocked in that test. |

## Architecture checks

The teammate's module boundaries are retained. Controllers handle HTTP and DI;
application services coordinate use cases; domain repository protocols describe
storage needs; infrastructure adapters own SQLAlchemy and TACC job control;
AI adapters own model transport, research providers, and workflow nodes. New
external integrations use ports registered through the composition root.

There are no changes to `backend/alcedo`, existing migrations, or database entity
tables. Schema migration remains at revision `0001`. Production and mock share
the workflow runner. Search follow-ups, guide retries, and workflow transitions
retain their explicit bounds. Task progress and model health use polling.

TACC connectivity and job renewal, source-backed request interpretation, metadata
and ratings, editorial reading links, interactive film timelines, compact task
titles, scrollable activity, and the pointer ripple are preserved.

## Verification

- 60 unit/component tests pass, including both new upstream tests and three new integration regressions.
- The frontend production build and `git diff --check` pass.
- An isolated PostgreSQL database migrates successfully and passes all four API smoke scenarios: Norse mythology, release filters, Romanian history, and an unsupported topic.
- The film-readings route resolves its dependencies and returns the expected response shape through the actual API.
- A browser run against the actual mock API covers input, parsing, criteria confirmation, research completion, five selected films, release ordering, zoom, film details, ratings, and readings.
- Browser fixtures cover successful and failed TACC renewal, cancellation of the confirmation dialog, ended-job resubmission, automatic setting fields, keyboard and mobile controls, task titles, activity scrolling at three viewport sizes, and ripple fade with reduced-motion support.
- A source-file credential-pattern scan reports no matches. This is a targeted scan rather than a security audit.

## Live TACC verification

The merged local application was restarted while its TACC allocation remained
running. Qwen completed request parsing, candidate discovery, assessment,
timeline generation, guide generation, validation, and publication for this input:

> Recommend three films about Romanian political memory released between 2000 and
> 2020. I prefer grounded political dramas and would like to understand their
> historical context.

Run `01a1db32-9fab-76fa-8699-b35905affbbc` finished with status `completed` in
346 seconds, including request parsing. It selected *The Autobiography of Nicolae
Ceaușescu*, *4 Months, 3 Weeks and 2 Days*, and *12:08 East of Bucharest*. All three
release years satisfy the requested range, and the generated guide includes each
selected film. This verifies one live case rather than recommendation quality
across all topics.

The live result also passed browser checks for three loaded posters, timeline
view switching and zoom, and film details. *12:08 East of Bucharest* displayed a
TMDB audience score of 6.9/10 with 128 votes and a BFI reading link. No browser
runtime errors were observed. Scores are snapshots taken during the check.

Local verification artifacts are under `.runtime/verification/merge-review/`,
including the parsed request, final response, mock result screenshot, and browser
checks. This ignored directory contains test artifacts and is not part of the
source changes. The isolated mock service and its temporary database were removed
after verification. The production app remains on port 8766 with the existing
TACC allocation.
