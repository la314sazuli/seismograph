# Evidence-backed investigations

Seismograph 0.3 adds a staff-driven investigation workflow to the existing
weekly signal detector. The design goal is to answer “what evidence should we
look for next?” rather than dress up a sentiment score.

## The workflow

- **Detect:** A published report supplies a signal with retained message evidence.
- **Challenge:** A staff-requested model pass proposes up to three explanations,
  the observations supporting and contradicting each, and a falsification test.
- **Ask:** The case suggests one discriminating question. Nothing asks users,
  creates a ticket, or contacts another service automatically.
- **Verify:** Staff records an intervention and refreshes the same case against
  recent messages. Python counts explicit outcomes from distinct local
  author pseudonyms after the marker.

This is a selected-evidence investigation, not a root-cause engine. Exact
substring checks establish quotation lineage, not whether the model correctly
understood a quote or whether a person's report is true.
Every case card shows its last-analysis time and selected evidence window.
Reading an old card does not re-run analysis or imply the state is current.

## Staff commands

The commands below are guild-only and require administrator permission at
invocation, not just a default command visibility setting. Replies are ephemeral.

```text
/seismograph_signals
/seismograph_case signal_id:12
/seismograph_case case_id:1
/seismograph_release case_id:1 note:"PDF citation patch deployed"
/seismograph_case case_id:1 refresh:true
/seismograph_case signal_id:24 case_id:1
```

The first command lists recent published signal IDs whose evidence remains in
the current source allowlist. The second creates a case. Supplying only a case
ID reads it without calling a model. A release marker records the current UTC
time; another marker replaces it. Supplying both IDs appends a new revision to
the chosen case and preserves its intervention marker.

`refresh:true` reads bounded recent history from the configured channels,
ending at the current time and spanning `ANALYSIS_DAYS`. It investigates the
existing case title using at most 120 selected recent messages; no new report
signal is needed. An empty usable window saves no new status. The previously
retained case must still be available to start this direct refresh.

Alternatively, staff chooses whether a later signal is the same issue by
supplying both IDs. The software does not infer stable issue identity from a
changed title. There is no background polling or automatic follow-up.

The case pass receives all the selected signal's retained seed messages, plus
bounded recent context from that report's configured source channels. It
refuses an oversized seed rather than silently dropping seed evidence.
Direct refresh instead selects recent messages without requiring old seed
messages, so it can examine successful experiences even if no new complaint
signal qualified for the weekly report. Context is capped at 120 messages in
one existing 24,000-character evidence
batch; the serialized JSON prompt adds framing overhead. Context selection is
recency-based, not semantic retrieval. A model can miss counterexamples outside
the selection, and irrelevant successful experiences must not be counted as a
fix: review its observations before relying on the status.

## Follow-up rules

After two retained revisions exist, `/seismograph_changes case_id:12` compares
their selected evidence and interpretations without a model call. It warns
when prior failure observations disappear or are relabeled, rather than
assuming the better status means recovery. Both snapshots use the current
intervention marker, not historical marker state. See
[case changes](case-changes.md) for exact scope and privacy behavior.

Staff can annotate an incorrect outcome through `/seismograph_reviews`,
`/seismograph_correct`, and `/seismograph_withdraw`. Original model counts stay
unchanged; a separately labeled preview applies current-revision corrections.
See [staff review](staff-review.md) for revision keys, audit and privacy limits.

The current revision's selected observations are the entire counting scope.
This is not a statistical estimate of the server's population.

| Condition | Displayed status |
| --- | --- |
| No intervention marker | No intervention recorded |
| At least one distinct post-marker failure reporter | Continued failures reported |
| No failure reporters and at least two distinct post-marker success reporters | Improvement corroborated, not proven resolved |
| Anything else | Insufficient follow-up; silence is not success |

Repeated messages do not create extra reporters. If one author reports both
success and failure in the selected follow-up, failure takes precedence.
Workarounds, unclear observations, and counterexamples do not count as successes.
A `success` must describe the affected workflow; successful normal answers do
not establish that PDF export was repaired. Relevant contrasting evidence from
another workflow is labeled `counterexample`, not `success`. This distinction
depends on model interpretation and must still be reviewed by staff. These are
conservative product rules, not confidence intervals or causal inference.

The marker establishes time order only. Actual patch adoption, matched device
versions, exposure, and reproducibility are not verified. A changed case
revision can change the status because it changes the selected evidence.

## Sonar and portability

Perplexity Sonar is the primary documented setup and is selected explicitly in
`.env.example`. Use these variables alongside the existing Discord configuration:

```sh
export LLM_PROVIDER=sonar
export LLM_BASE_URL=https://api.perplexity.ai
export LLM_MODEL=sonar
# Supply LLM_API_KEY securely; never commit it.
```

The adapter uses chat completions and JSON Schema output, and sets
`disable_search=true` for private report and case analysis. Perplexity documents
the compatible endpoint in its [OpenAI compatibility guide](https://docs.perplexity.ai/docs/sonar/openai-compatibility),
structured output in [Sonar features](https://docs.perplexity.ai/docs/sonar/features),
and search controls in [Sonar filters](https://docs.perplexity.ai/docs/sonar/filters).

Search disabled does not mean local processing: the selected redacted message
text still goes to the configured provider. Complete the existing processing
approval and community-disclosure checklist. Redaction is best-effort, and
disabling search is not a guarantee of provider retention or training policy.

For another server, `LLM_PROVIDER=openai` keeps the original JSON-object
chat-completions contract. A compatible self-hosted endpoint is possible; verify
its JSON behavior and output quality. Here `openai` names the API compatibility
adapter, not a requirement to use OpenAI's models or hosting. Requests go only
to the configured base URL; the bot does not automatically switch providers.
The omitted-variable fallback remains `openai` for compatibility with existing
installations, so set `LLM_PROVIDER=sonar` explicitly for the Sonar setup.

Sonar is the primary setup example, not a mandatory dependency or an exclusive
feature tier. No free or unlimited provider access
is bundled with this MIT project.

Sonar request shape, request caps, search isolation, and returned-source
handling are tested with synthetic responses. No live Sonar call or semantic
quality benchmark has been performed. The provider layer is deliberately small
so endpoint changes do not require rewriting the case lifecycle.

## Optional public research

The CLI exposes a separate, operator-initiated public research lane:

```sh
python -m seismograph research-public \
  "Public documentation about PDF export and citation links" \
  --domains docs.perplexity.ai,perplexity.ai \
  --approved-public-query
```

This makes a provider call and currently needs the full environment
configuration, with `LLM_PROVIDER=sonar`. No case ID, database connection, or
Discord object enters the research function. It enables search only for the
explicit query, with one to five operator-selected source domains.

The approval flag attests that the operator reviewed the exact query for
public disclosure. Pattern checks reject obvious personal identifiers, but do
not detect every confidential fact or person name. Never copy private messages
into this command. Research is never triggered by a Discord message.

Only HTTPS URLs returned in the provider's `search_results` and matching an
allowed domain or subdomain are accepted as sources. URLs invented in the JSON
answer are not used as source provenance. An allowed source does not prove that
every sentence in the summary is supported: read it before use. Public findings
are labeled as leads, not causes, and are not automatically attached to cases.

## Privacy, failure handling, and storage

- **Atomic saves:** Report and case evidence snapshots are checked under the
  SQLite writer lock. An opt-out or edit before persistence rejects stale
  analysis; a later deletion removes the affected local derived records.
- **Conservative invalidation:** Every input message, not just quoted messages,
  is linked to the case revision. A deletion or content edit affecting any
  revision removes all revisions of that case, preventing fallback to an older
  interpretation. A later authorized refresh can recreate evidence.
- **Retention:** Pruning messages also invalidates affected cases. Empty old
  case records are removed by retention. Staff-authored intervention metadata
  is not a model-derived message summary.
- **Bounds:** One case analysis runs at a time alongside the report lock, with
  at most two validation attempts and at most six HTTP attempts, further
  limited by `MAX_LLM_REQUESTS`. Unusable output is not saved.
- **Cancellation:** A cancelled case command may leave an in-flight provider
  call finishing, but that worker cannot persist the case. Operator logs
  remain important if the Discord interaction itself fails.
- **Migration:** Back up before first opening an old database. Schema v3 adds
  case tables, lineage, and invalidation triggers. No new runtime dependency,
  queue service, vector database, or agent framework is required.

Already-sent reports, ephemeral responses, exported documents, provider copies,
and backups are outside local SQLite deletion. They require separate operator
handling. Do not claim that an opt-out retracts already-disclosed information.

## Reproduce without Discord

```sh
python -m seismograph investigate-demo
pytest tests/test_cases.py tests/test_premerge_regressions.py
pytest
ruff check .
ruff format --check .
```

The demo includes a plausible but contradicted broad explanation deliberately:
it shows why staff should inspect counterexamples, not treat every proposed
hypothesis as equally credible. It does not establish model accuracy. An
authorized live pilot and representative human-scored model evaluation remain
necessary before any production deployment.
