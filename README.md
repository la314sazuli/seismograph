# Seismograph

Find the failure. Challenge the explanation. Check the fix.

[![checks](https://github.com/la314sazuli/seismograph/actions/workflows/checks.yml/badge.svg)](https://github.com/la314sazuli/seismograph/actions/workflows/checks.yml)

Seismograph turns Discord feedback into evidence-backed investigations, not just
a sentiment chart. It groups recurring complaints, gives staff competing
explanations with counterexamples, and checks reported outcomes after an
intervention. It is an independent, MIT-licensed, self-hosted project, not an
official Perplexity integration.

## Try the difference

```bash
python -m seismograph investigate-demo
```

No credentials, network, or Discord server needed after installation. The
fictional scenario starts with missing citations, distinguishes PDF-export
failures from successful normal answers, proposes one useful follow-up question,
and refuses to mark a patch successful while failures continue.

The output uses recorded interpretations, not a live model. Exact quote checks,
case persistence, deterministic reporter counts, and deletion checks run through
the real implementation. See the [investigation guide](docs/investigations.md)
for staff commands, Sonar configuration, and the current limits.
Read the [recorded demo output](docs/investigation-demo.md) to inspect the case
without installing anything.

To inspect what changed between investigations, try
`python -m seismograph changes-demo`. The staff-only `/seismograph_changes`
command separates newly selected evidence from rewritten interpretations and
flags apparent recovery caused by omitted failure observations. Read
[the change-log guide](docs/case-changes.md) for its limits.

To challenge a wrong outcome label without rewriting the evidence, try
`python -m seismograph review-demo`. Staff can record revision-bound corrections
and withdrawals, with a separately labeled count preview and privacy-aware
audit. See [staff review](docs/staff-review.md) for commands and upgrade steps.

To distinguish patch exposure from a post-release timestamp, try
`python -m seismograph exposure-demo`. Staff can separate quote-backed `received`
and `not_received` observations from `unknown`, without rewriting outcomes or
claiming a rollout success rate. New revisions and release markers reset exposure
to unknown. See [patch exposure](docs/patch-exposure.md) and the
[recorded replay](docs/exposure-demo.md).

## Evaluate without Discord

Run `python -m seismograph evaluate --output evaluation-smoke.json` for an
offline scorer check against 12 synthetic challenge cases. This default
reference replay uses the answer key and is **not a model-quality result**.
An optional, explicitly approved live mode tests Sonar or another
OpenAI-compatible model without Discord credentials. The
[evaluation guide](docs/evaluation.md) explains answer-key separation, false
reassurance checks, request budgets, audit files, and the human-review rubric.
The [fresh challenge and review pack](evaluations/fresh-v1/README.md) provides
ten separate ambiguous scenarios and a provider-blinded A/B review workflow,
including an optional simple-summary baseline. No real-model results are bundled.

## What it does

- Reads only allowlisted text channels or explicitly listed public threads in
  one guild, without accessing DMs or downloading the member list.
- Separates `broken`, `blocked`, `confusing`, `missing`, `disliked`, and `praise`.
- Counts distinct pseudonymous authors, caps repetition's influence, and ranks
  signals with a transparent deterministic score.
- Preserves message-level evidence through batching and merging. The model
  cannot supply user counts, scores, or invented evidence IDs.
- Supports weekly catch-up scheduling and administrator-only `/seismograph`.
- Stores redacted content in SQLite, supports `/seismograph_optout`, and handles
  edits, deletions, retention, and administrator feedback reactions.
- Bounds collection and model requests; stops rather than publishing partial
  analysis when a limit or validation check fails.
- Creates persistent, staff-requested cases with source-checked quotations,
  competing hypotheses, counterexamples, and a test that could refute each.
- Records an intervention without closing the case. Explicit failure and success
  reports drive follow-up status; silence is never evidence of a fix.
- Supports Sonar with search disabled for private analysis, or a portable
  OpenAI-compatible endpoint. Optional public research is separately approved.

This is a working MVP with large-community safeguards, not a claim of validated
production capacity for any particular guild. Read the
[large-community rollout checklist](docs/operations.md) before processing real data.

## Install and try it

Use Python 3.12+ on Linux or macOS, or the Linux Docker image.
For versioned downloads, checksums, source archives, and database upgrade
instructions, see the [release guide](docs/releases.md). A release is not a
production-capacity certification, and a draft is not a public download.

```bash
git clone https://github.com/la314sazuli/seismograph.git
cd seismograph
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m seismograph demo
```

Run `python -m seismograph --version` to identify the installed version without
connecting to a provider or Discord.

The default demo needs no credentials or paid API calls. It uses clearly labelled
fictional fixtures and a recorded classification, then runs real preprocessing,
evidence validation, scoring, and rendering. `demo --live` instead uses the
configured model and currently requires full configuration.

An abbreviated synthetic report:

```text
Seismograph - Weekly Friction Report
Period: August 3–9 (synthetic)
Messages analyzed: 42
Distinct contributors: 25

Saved workspace filters reset after reopening
Category: broken
Tremor Score: 47/100
Distinct users: 7
Supporting messages: 9
Trend: rising
Severity: 4/5
Confidence: 0.88

Expected: Saved filters should remain when the workspace is reopened.
Reported: Users report missing filters after closing and reopening.
Evidence: Up to three original Discord jump links.
Next step: Attempt reproduction with saved filters and a new session.
```

Reports include strongest tremors, emerging signals, independent praise, and up
to three unranked signals needing human review. Main reports require at least
2 distinct users, 3 supporting messages, and 0.5 confidence; praise requires
3 users and 0.6 confidence. When no signal qualifies, nothing is posted.

## Discord setup

1. Create an application and bot in the Discord Developer Portal.
2. Enable Message Content Intent and obtain any required privileged-intent
   approval. Server Members and Presence intents are unnecessary.
3. Invite with `bot` and `applications.commands` scopes.
4. Grant View Channel and Read Message History only in the selected sources.
5. Create a staff-only text channel, deny `@everyone` View Channel, and grant
   the bot View Channel, Read Message History, Send Messages, and Add Reactions.
6. Check all custom role overwrites. Report readers must be authorized to see
   information from every source channel.
7. Disclose collection, provider processing, retention, opt-outs, and a private
   support contact to the community before starting.

The bot checks source/destination permissions at startup and before reporting.
Public threads need their own IDs; allowlisting a parent does not include its
threads. Forum containers and private threads are not supported.

Discord's [Gateway documentation](https://discord.com/developers/docs/events/gateway)
describes Message Content Intent and review requirements. The
[Developer Policy](https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy)
restricts data use and prohibits model training on message content without
express permission. Server approval is not a substitute for required platform
approval or an acceptable provider data policy.

## Model provider: Perplexity Sonar first

Perplexity Sonar is the primary documented setup. Copy `.env.example` and
supply your own Perplexity API key securely:

```env
LLM_PROVIDER=sonar
LLM_BASE_URL=https://api.perplexity.ai
LLM_MODEL=sonar
LLM_API_KEY=replace-with-your-perplexity-api-key
```

The Sonar adapter requests structured JSON and disables web search for private
Discord analysis. Disabling search does not make processing local: selected,
redacted message text still goes to the configured provider. Optional public
research is a separate, explicitly approved operation.

Other servers can choose a hosted or self-hosted compatible endpoint using
`LLM_PROVIDER=openai` and that provider's URL, model, and key.
**OpenAI-compatible describes the API format, not a requirement to use OpenAI's
models or hosting.** Requests go to `LLM_BASE_URL`; there is no automatic
fallback to another provider.

The source remains MIT-licensed and provider-portable. It does not include API
access, unlimited inference, or an endorsement by Perplexity. The Sonar adapter
is covered by offline request-contract tests, not a live-model quality claim.
See [Sonar setup and portability](docs/investigations.md#sonar-and-portability).

## Configuration

All configuration comes from environment variables. `.env` files are not
automatically loaded.

| Variable | Default | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | required | Bot token. |
| `DISCORD_GUILD_ID` | required | Single guild ID. |
| `SOURCE_CHANNEL_IDS` | required | Comma-separated explicit source IDs. |
| `REPORT_CHANNEL_ID` | required | Staff-only text channel, not a source. |
| `LLM_API_KEY` | required | Perplexity API key for the Sonar setup, or your selected provider's key. |
| `LLM_BASE_URL` | required | Sonar: `https://api.perplexity.ai`. Other providers may use a compatible URL; localhost HTTP is allowed. |
| `LLM_MODEL` | required | Sonar setup: `sonar`. Otherwise a model supporting the compatible JSON contract. |
| `LLM_PROVIDER` | `openai` if omitted | Set `sonar`, as in `.env.example`. The omitted-variable fallback stays unchanged for existing compatible-endpoint installations. |
| `AUTHOR_HASH_SALT` | required | Random secret, at least 16 characters. |
| `LLM_PROCESSING_APPROVED` | `false` | Must be `true` to run the bot after completing the rollout checklist. |
| `REPORT_TIMEZONE` | `UTC` | IANA timezone. |
| `REPORT_WEEKDAY` | `0` | Monday=0 through Sunday=6. |
| `REPORT_HOUR` | `9` | Local hour, 0–23. |
| `ANALYSIS_DAYS` | `7` | Whole local days per report. |
| `RETENTION_DAYS` | `30` | Must be at least analysis days plus 7 for catch-up. |
| `MAX_MESSAGES` | `20000` | Total scanned history and stored analysis cap per report. |
| `MAX_LLM_REQUESTS` | `200` | HTTP attempt cap per report, including retries. |
| `DATABASE_PATH` | `seismograph.db` | SQLite path; use `/data/seismograph.db` in Docker. |

Generate the author secret locally, for example with
`python -c 'import secrets; print(secrets.token_hex(32))'`. Never commit it or
rotate it without planning for broken opt-outs and historical author linkage.

For a shell run, create and edit a local `.env` from `.env.example`, protect its
permissions, then load your own trusted file:

```bash
chmod 600 .env
set -a
source .env
set +a
export DATABASE_PATH=./data/seismograph.db
python -m seismograph run
```

Do not source a file from an untrusted contributor. No real `.env`, database,
credentials, or user messages should be committed.

## Operation

```bash
python -m seismograph period        # current manual analysis window
python -m seismograph runs          # recent run states
python -m seismograph runs --retry 3 # only a failed pre-publication scheduled run
python -m seismograph prune         # apply retention without network credentials
```

The scheduler checks every five minutes and catches up the latest due weekly
window on startup, so it can start model processing immediately. Scheduled
periods are claimed before analysis; ambiguous send failures are never
automatically reposted. This favors duplicate avoidance over guaranteed delivery.
See [recovery procedures](docs/operations.md#schedule-and-recovery).

Only one process may use a database and only one report runs at a time.
The slash command acknowledges immediately; long-run results and failures are
reported in operator logs, avoiding expiring interaction tokens.

### Docker

```bash
docker build -t seismograph .
docker run --rm --env-file .env \
  -e DATABASE_PATH=/data/seismograph.db \
  -v seismograph-data:/data seismograph
```

The image runs as UID 10001. Mount persistent local storage at `/data`; do not
put secrets in the image or use multiple replicas against one database.

## Architecture

`bot.py` collects bounded history and live events without member caching.
`privacy.py` redacts obvious sensitive strings and HMACs author IDs.
`storage.py` stores messages and run state directly in SQLite with WAL.
`analysis.py` prepares bounded text batches and calls Sonar or a compatible
chat-completions endpoint in a worker thread.
Validated candidates merge through bounded descriptor groups while Python
retains and unions the original evidence IDs.
`scoring.py` computes deterministic rankings, and `report.py` renders
length-limited, mention-disabled reports.
The scheduler, manual command, and synthetic demo reuse the same domain logic.
`cases.py` validates case evidence and computes follow-up status;
`case_commands.py` exposes administrator-only ephemeral workflows.
`case_history.py` compares retained snapshots without a model or new storage.
`case_reviews.py` keeps revision-bound staff corrections separate from model output.
`research.py` accepts only an explicitly approved public query and source
domains, never a database or case object. There is no autonomous research loop.

## Tremor Score

Each component is clamped to `[0, 1]`; `m` means supporting messages.

```text
B = log1p(distinct_users) / log1p(50)
S = (severity - 1) / 4
G = log((m + 1) / (previous_m + 1)) / log(4)
V = log1p(m / period_days) / log1p(10)
N = 1 if previous_m == 0 else 0
R = log1p(max(0, m - distinct_users)) / log1p(20)

score = round(100 * (.40*B + .25*S + .13*G + .12*V + .05*N + .05*R)
              * (.4 + .6*confidence))
```

Missing comparable history uses `G=0.35` and `N=0.5`. Repetition contributes at
most five percentage points before the confidence discount. Twenty messages
from one user do not beat ten independent reports solely through volume under
otherwise identical inputs.

History matches normalized title/category in an immediately preceding,
equal-duration published window. Overlapping manual reports are excluded.
Changed titles and unavailable comparable periods can make trends unreliable.
A signal is `emerging` with at least 3 users, at most 1 prior message, a span
no longer than 48 hours, confidence at least 0.6, and fewer than half the messages
of the largest non-praise cluster.

## Privacy and limitations

Author IDs are HMAC-SHA256 pseudonyms; author hashes never appear in reports.
Email addresses, phone-like strings, tokens, mentions, IP addresses and invite
links are redacted on a best-effort basis. Summaries may still expose sensitive
facts, so review provider settings and access controls before using real data.

Opt-out deletes local user messages, affected signals, and all revisions of
affected cases while retaining a suppression hash. Retention also removes
expired reports, evidence and feedback
from SQLite. Already-posted Discord reports, provider-held data, and backups
need separate operator handling; there is no legal-compliance guarantee.

Staff corrections and withdrawals cascade with invalidated case revisions.
Reviewer opt-out removes their review records without reactivating withdrawn
corrections. Patch-exposure assessments also cascade with invalidated revisions;
reviewer opt-out erases affected observation ledgers without restoring older
assessments. See [staff review](docs/staff-review.md) for correction behavior and
[patch exposure](docs/patch-exposure.md) for privacy and schema-v5 upgrade details.

Other limitations:

- No live large-guild validation or live model-quality benchmark is included.
- Bounded merge groups can leave semantically duplicate signals across groups.
- Evidence membership is validated, not the truth of model-written conclusions.
- Feedback covers administrators on the report's first message, not each signal.
- No attachment/embed analysis, automatic thread discovery, dashboard, or vector DB.
- Limits stop oversized reports rather than silently sampling the population.
- SQLite and the process lock are for one local instance, not horizontal scaling.
- Case hypotheses, relevance, and outcome labels are model proposals, not
  verified causes or measured causal effects. Staff must review them.
- Case context is capped at 120 selected messages. It is not a census or an
  automatic cross-period identity matcher.

## Contributing and checks

```bash
pytest
ruff check .
ruff format --check .
python -m seismograph demo
python -m seismograph investigate-demo
python -m seismograph changes-demo
python -m seismograph review-demo
python -m seismograph exposure-demo
python tools/benchmark.py --messages 20000
```

The benchmark is local and synthetic, with a recorded classifier; it measures
storage and deterministic processing, not live Discord traffic or model accuracy.
CI also builds the Docker image and checks its demo and non-root runtime.

Keep changes focused, dependencies few, and abstractions justified by current
needs. See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
The [MIT License](LICENSE) permits use, modification, distribution and commercial
use subject to its terms.
