# Local validation

Measured on 2026-09-16 UTC in a Linux sandbox with Python 3.12, 2 vCPUs and 8 GiB RAM.
These are synthetic engineering checks, not production capacity or model-quality
results.

| Check | Result |
| --- | --- |
| `pytest` | 338 passed in 6.39 seconds; one upstream `audioop` deprecation warning |
| Original offline integration pilot | 11 passed, included in the 338-test total |
| Investigation and pre-merge regression coverage | 53 additional tests, included in the total |
| Evaluation regression coverage | 32 tests, included in the total; malformed predictions, false reassurance, answer-key separation, and bounded requests |
| Fresh-set and review-tool coverage | 19 tests, included in the total; frozen hashes, packet metadata separation, empty ratings, missing outputs, baseline opt-in and budgets |
| Case change-log coverage | 23 tests, included in the total; context versus interpretation, lost failures, privacy invalidation, current-marker comparisons, stale sends, and staff-only command handling |
| Staff-review coverage | 43 tests, included in the total; correction previews, withdrawal audits, fresh revision keys, v3-to-v4 migration, opt-outs, allowlists, pagination, and command privacy |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed |
| `python -m seismograph demo` | Passed without credentials or network calls |
| `python -m seismograph investigate-demo` | Passed without credentials or network calls |
| `python -m seismograph changes-demo` | Passed without credentials or network calls; demonstrates omitted failure evidence and privacy-invalidated history |
| `python -m seismograph review-demo` | Passed without credentials or network calls; deliberately wrong recorded label corrected without altering source/model output, withdrawal, and privacy erasure |
| `python -m seismograph evaluate` | All 12 reference-replay cases passed; zero provider calls; scorer consistency only, not model accuracy |
| Fresh review preparation | Ten additional cases; blank two-reviewer packet generated; zero real provider calls or human ratings |
| Build and install | Built the updated 0.3.0 wheel, installed in a fresh Python 3.12 environment, ran all four demos and the evaluator outside the source tree; fresh review tooling is source-checkout-only |
| Installed dependencies | Compatible according to `uv pip check` |
| 20,000 synthetic messages | 1.410 seconds total; 72.5 MiB peak RSS; 92 recorded-classifier batches |
| 100,000 synthetic messages, earlier 0.2 baseline only | 5.104 seconds total; 162.6 MiB peak RSS; 459 recorded-classifier batches; not rerun for 0.3 |

The benchmark exercises SQLite ingestion, preprocessing, validation,
evidence-preserving title merging, measured author counts, scoring, and rendering.
Every synthetic message describes the same issue, so this is not a diverse-topic
clustering benchmark. The 100,000-message run deliberately overrides the default
message limit and substitutes a classifier with no HTTP calls; it would exceed
the default 200-request allowance with a live model.

The unit suite separately checks bounded multi-topic merge groups, invalid
responses, retries, event-loop responsiveness, scope isolation, opt-outs, schema
migration, scheduling, changed evidence, and ambiguous send failures.

Docker is not available in this local sandbox. The `checks` workflow runs a
Python 3.12, 3.13, and 3.14 matrix and a separate Docker job, including the
investigation, case changes, and staff review demos plus the offline evaluator
inside the image. Check the CI results on the exact current
head of [PR #2](https://github.com/la314sazuli/seismograph/pull/2) before relying
on them; earlier Docker results do not validate a newer revision.
No live Discord integration test or real-model quality evaluation was performed.

## Staff-review checks

`tests/test_case_reviews.py` verifies that human annotations never overwrite
the source messages or original model payload. Preview counts are separate,
withdrawals retain both reasons, duplicate active corrections are refused,
and revisions never silently inherit corrections.

The migration test preserves existing v3 payloads and assigns distinct revision
keys. Another regression deletes and recreates a revision with the same numeric
SQLite ID: the old opaque key is rejected. Other checks cover invalid outcomes,
missing observations, stale keys, redaction, scope restrictions, bounded audit
pagination, and opt-outs by either the correction author or withdrawal author.
Erasing a withdrawal cannot reactivate its parent correction.

Mocked Discord commands test runtime administrator checks, private replies,
permission-preflight failures before writes, busy locks, no model calls, and
stopping later reply chunks after privacy erasure. Normal case and change-log
cards show an active-review warning without changing their original counts.
These are synthetic engineering checks, not independent human review or a
guarantee that a staff correction is correct. See [staff review](staff-review.md).

## Case change-log checks

`tests/test_case_history.py` covers the deterministic comparison of two retained
investigation revisions. It distinguishes new selected context from newly cited
old context, omitted observations, and rewritten interpretations. It explicitly
warns when post-marker failure evidence disappears even though the source
message remains available. A status improvement caused by that omission is not
treated as proof of recovery.

Both snapshots are evaluated under the current intervention marker, not
historical marker states. Checks cover changed markers, new revisions,
out-of-allowlist context, malformed snapshots, the 120-message bound, and
preservation of an existing database transaction. Deletion, editing, opt-out,
and retention each invalidate affected history rather than restoring an old
snapshot.

The command tests exercise runtime administrator checks, private replies,
disabled mentions, report-lock contention, and stopping subsequent chunks after
privacy invalidation. They do not establish an atomic guarantee between a local
database check and an in-flight Discord send; already-sent content cannot be
recalled by these checks. See the [case changes guide](case-changes.md).

## Evaluation checks

The [evaluation guide](evaluation.md) documents the twelve author-defined
synthetic scenarios, separate answer key, exact scoring denominators, explicit
API opt-in, and unscored human-review dimensions. The default replay deliberately
uses gold labels and must not be presented as model accuracy.

Regression tests show that deliberately labeling different-workflow successes
as fix evidence produces a false-reassurance failure. Other checks cover
omissions, irrelevant evidence, invented quotes, unknown IDs, missing
counterexamples, all-missing predictions, global retry budgets, input validation,
output overwrite prevention, and saved-report rescoring. Mocked HTTP verifies
the real collector omits the answer key and author hashes from Sonar requests.
No real provider was called.

The [fresh-v1 review set](../evaluations/fresh-v1/README.md) contains ten separate
AI-authored cases, not an independent holdout. Its tests confirm that reference
labels agree with current rules, while the review notes explicitly identify
correction and rollout-exposure limits in those rules. Neither the production
prompt nor verification logic was changed to make this set pass. The summary
baseline has no automatic investigator score; comparison requires human review
using the common rubric.

## Investigation checks

`tests/test_cases.py` and `tests/test_premerge_regressions.py` cover the new
workflow and the two defects found in the earlier pre-merge review:

- Exact quote validation; unknown, duplicate, malformed, or conflicting
  observation references fail closed.
- Bounded evidence context, one repair attempt, and no author hashes in case
  prompts.
- Persistent identity and intervention markers, both v1 and v2 migration paths,
  and unchanged historical message data.
- Edit, deletion, opt-out, and retention invalidation, including uncited context
  and prevention of fallback to an old case revision.
- Transactional rejection of stale message snapshots and stale prior-case
  revisions used as input during a refresh.
- Distinct-reporter counting, failure precedence, and refusal to interpret
  silence as success.
- Sonar search disabled for private analysis, explicit JSON Schema payloads,
  portable-provider request shape, and request-budget enforcement.
- Approval and obvious-identifier rejection before public research calls,
  allowlisted returned-source handling, and rejection of invented source URLs.
- Real case-command storage and ephemeral-response handling with mocked Discord,
  including direct refresh without another published report signal.
- Runtime administrator checks on all three investigation commands, not just
  default visibility settings.
- Case cards identify their last-analysis time and selected evidence window.
- Cancellation cannot let the case's provider worker write derived records.
- Opt-out between report analysis and save no longer restores excluded signal
  evidence, even with a later permissions failure configured.
- Discord's actual history iterator preserves the inclusive start boundary
  during history reconciliation.

These tests use recorded or synthetic model output. They verify code contracts,
not semantic correctness, diagnosis quality, prompt-injection resistance, or
platform permission behavior in a real guild.

## Credential-free integration pilot

`tests/test_offline_pilot.py` runs the real collection, privacy filtering, SQLite
storage, analysis, scoring, scheduling, rendering, and publication-state code.
The model client makes real HTTP requests to a temporary loopback server, which
returns recorded synthetic classifications in the expected response envelope.
Discord channel objects, history, events, permissions, and sends are simulated.
The test clock is fixed so scheduler and retention checks are repeatable.

The fixture contains 46 synthetic messages, folded into two source channels.
The successful report analyzes 42 messages after filtering. No real community
data, Discord credentials, or paid model calls are used, and no messages are
sent to Discord.

| Scenario | Verified result |
| --- | --- |
| Two-channel report | Correct analyzed-message and distinct-user counts, review and counter-signal sections, evidence links, and chunks within 2,000 characters |
| Privacy boundary | Fixture email, IP address, and token removed before HTTP; local author hashes absent from the prompt and report |
| Instruction separation | Injection-like fixture text stays in user-supplied evidence, not the system prompt; this does not prove a live model will resist injection |
| Scheduler restart | Reopening the same database does not repeat a completed scheduled report or model request |
| Temporary HTTP failure | One simulated HTTP 503 is retried successfully |
| Malformed model output | One malformed response is repaired; repeated malformed output stops after two requests without posting |
| Public report channel | Simulated visibility to everyone prevents analysis and posting |
| Unreadable source | Missing simulated read permissions prevents analysis and posting |
| Opt-out | The command removes local author evidence and subsequent history backfill does not restore it |
| Edits and deletions | Changed content reaches the analysis snapshot; deleted evidence does not |
| Staff feedback | Non-administrator reaction is ignored; administrator feedback is recorded and removable |
| Collection limit | Exceeding the configured message cap stops before any model request or post |

These scenarios are covered by 11 tests; some tests assert multiple boundaries.
The original pilot added no production dependencies. The 0.3 investigation
workflow also adds no runtime dependencies.

## Reproduce

From the repository root, with Python 3.12 and the development dependencies
installed:

```sh
python -m pip install -e '.[dev]'
python -m pytest tests/test_offline_pilot.py -o addopts='' -q
python -m pytest -o addopts='' -q
ruff check .
ruff format --check .
python -m seismograph demo
python -m seismograph investigate-demo
python -m seismograph changes-demo
python -m seismograph review-demo
python -m seismograph evaluate --output evaluation-smoke.json
python tools/blind_review.py prepare --output fresh-review
```

The tests supply fake configuration and temporary storage; do not add real
credentials. The offline integration tests require permission to bind a local
loopback port. Installing dependencies requires network access if they are not
already installed.

## Remaining live checks

The passing suite is not a production approval for a 500,000-member server.
It does not establish actual Discord permissions, gateway delivery, rate-limit
handling under live traffic, target-host reliability, or real-model accuracy and
prompt-injection resistance. Those still require an authorized, limited live
pilot and representative model evaluation before production use.
