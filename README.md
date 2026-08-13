# Seismograph

Detect friction before it becomes an incident.

[![checks](https://github.com/la314sazuli/seismograph/actions/workflows/checks.yml/badge.svg)](https://github.com/la314sazuli/seismograph/actions/workflows/checks.yml)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Seismograph turns Discord feedback into evidence-backed product signals. It groups
recurring complaints, identifies emerging friction, separates widespread problems
from individual loudness, and produces a concise weekly report with links to the
original messages.

Individual complaints are small tremors. Seismograph exists to notice when those
tremors form a pattern, while the pattern is still small enough to act on.

It is not a conversation summarizer. It answers a narrower set of questions: what
are users struggling with, how many distinct people are affected, which problems
are growing, what did users expect, what did they observe instead, and which
messages support each conclusion.

## What it does

- Reads messages only from explicitly allowlisted channels in one guild. Direct
  messages are never accessed.
- Stores a message id, channel id, timestamp, redacted content, and an HMAC of the
  author id. Usernames are never stored.
- Classifies each signal as `broken`, `blocked`, `confusing`, `missing`,
  `disliked`, or `praise` instead of positive/negative sentiment.
- Weights distinct users above raw message volume, so one prolific reporter cannot
  take over the report.
- Computes a deterministic Tremor Score in application code. The model never
  decides ranking.
- Flags small, fast-forming, multi-user clusters as `emerging` before they become
  the loudest topic.
- Attaches an evidence packet with up to three jump links to every signal, and
  rejects any signal whose supporting message ids were not in the analyzed input.
- Shows well-supported praise as a separate counter-signal section, never netted
  against complaints.
- Publishes nothing when no signal clears the evidence thresholds.
- Posts weekly on a schedule and on demand via an administrator-only
  `/seismograph` command, recording completed periods so a scheduled report is
  never posted twice.
- Collects maintainer feedback through four reactions on the posted report.

## Sample report

Produced by `python -m seismograph demo` from the synthetic fixtures, abbreviated:

```text
# Seismograph — Weekly Friction Report

Period: August 3–9 (synthetic)
Messages analyzed: 42
Distinct contributors: 25
Signals with sufficient evidence: 3

## Strongest Tremors

### 1. Saved workspace filters reset after reopening

Category: broken
Product surface: saved filters
Tremor Score: 47/100
Distinct users: 7
Supporting messages: 9
Trend: rising
Severity: 4/5
Confidence: 0.88

Expected: Saved filters should still be present the next time the workspace is opened.
Reported: Users report that saved filters are missing after closing and reopening the
workspace, while a plain refresh keeps them.

Evidence:
- https://discord.com/channels/.../.../900000000000000137
- https://discord.com/channels/.../.../900000000000000274
- https://discord.com/channels/.../.../900000000000000411

Suggested next step: Attempt reproduction by saving filters, fully closing the
workspace, and reopening it in a new session.

## Emerging Signals
## Counter-Signals
## Needs Human Review
```

## Architecture

Messages arrive through `bot.py`, are redacted and pseudonymized immediately, and
are written to SQLite by `storage.py`. When a report is requested, `pipeline.py`
selects the window of whole local days, backfills anything missing from the
allowlisted channels, and hands the messages to `analysis.py`. Stage one of
analysis is deterministic: it drops bot messages, empty messages, bare commands
and one-word acknowledgements, normalizes whitespace, and splits the remainder
into context-sized batches while preserving every message id. Stage two sends each
batch to an OpenAI-compatible chat-completions endpoint with the prompts in
`prompts.py` and asks only for classification and evidence selection. Every model
response is validated against the actual input: unknown message ids, malformed
categories, out-of-range severities, and signals without evidence are rejected,
and counts, dates and distinct users are measured from the referenced messages
rather than read from the model. A failed validation earns exactly one repair
request before the run is abandoned without publishing. `scoring.py` then attaches
trends and Tremor Scores in plain arithmetic, `report.py` sorts signals into
sections and renders Markdown split to fit Discord's message limit, and the run,
its signals, and their evidence links are stored so the next report can measure
growth and so a scheduled period is never processed twice.

## Tremor Score

A signal's score is a weighted sum of six normalized components, discounted by
confidence, then rounded to an integer between 0 and 100:

```text
breadth  = log1p(distinct_users) / log1p(50)                    weight 0.40
severity = (severity - 1) / 4                                   weight 0.25
growth   = log((m + 1) / (previous_m + 1)) / log(4)             weight 0.13
velocity = log1p(messages_per_day) / log1p(10)                  weight 0.12
novelty  = 1 if previous_m == 0 else 0                          weight 0.05
repeat   = log1p(max(0, m - distinct_users)) / log1p(20)        weight 0.05

score = 100 * weighted_sum * (0.4 + 0.6 * confidence)
```

Every component is clamped to `[0, 1]`, so a large community cannot produce an
unbounded score. Breadth carries eight times the weight of repetition, and
repetition saturates after about twenty extra messages: twenty messages from one
user cannot outrank ten reports from ten users. When no comparable previous period
is stored, growth and novelty use neutral values (`0.35` and `0.5`) so a first run
neither inflates nor suppresses scores. Signals are matched across runs by
normalized title and category; a title that changes between runs is treated as
unseen.

A signal is relabelled `emerging` when it has at most one message in the previous
period, at least three distinct users, all supporting messages inside a 48-hour
window, confidence of at least 0.6, and fewer messages than half of the largest
non-praise cluster.

## Report sections

- **Strongest Tremors** — at least 2 distinct users, at least 3 supporting
  messages, confidence at least 0.5.
- **Emerging Signals** — clears the same bar and matches the emerging rule.
- **Counter-Signals** — `praise` with at least 3 distinct users and confidence at
  least 0.6. Praise is never subtracted from a complaint's severity or score.
- **Needs Human Review** — at most three signals that missed the bar on breadth or
  confidence, labelled unconfirmed and excluded from the ranking.

If every section would be empty, no report is published.

## Installation

Requires Python 3.12 or newer.

```bash
git clone https://github.com/la314sazuli/seismograph.git
cd seismograph
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Local demo

The demo runs on synthetic fixtures with no Discord token and no paid API call:

```bash
python -m seismograph demo
```

It loads `seismograph/fixtures/synthetic_messages.json`, runs the real deterministic
preparation and validation, substitutes a recorded structured analysis from
`seismograph/fixtures/synthetic_analysis.json`, computes real Tremor Scores, and prints the
Markdown report with a synthetic-data notice. All fixture content is fictional.

Add `--live` to call the configured LLM instead of the recorded analysis. That
path requires full configuration and will incur provider cost. A live run prints
a summary to stderr before the report so a provider or prompt change can be
judged on numbers rather than impressions:

```text
Live analysis summary
  Messages analyzed: 42 in 1 batch(es); largest batch 42 message(s), 4910 prompt characters
  Model requests: 2
  Batches valid on first attempt: 0 of 1
  Repair attempts: 1, recovered: 1
  Candidates rejected: 1
    unknown_message_ids: 1
  Signals accepted: 5 (no merge pass needed)
```

Every rejection carries a stable `kind`, so the counts show which schema rule a
model actually struggles with. Reasons from a first attempt are kept even when
the repair succeeds. Scheduled and on-demand runs log the same summary.

## Discord application setup

1. Create an application and a bot user in the Discord Developer Portal.
2. Under **Bot → Privileged Gateway Intents**, enable **Message Content Intent**.
   Server Members and Presence are not needed.
3. Invite the bot with the `bot` and `applications.commands` scopes and these
   permissions: View Channels, Read Message History, Send Messages, Add
   Reactions. Nothing else is required.
4. Restrict the bot's channel access to the channels you intend to analyze.
5. On first start the `/seismograph` command is registered to your guild. It is
   marked administrator-only; server administrators can further restrict it under
   **Server Settings → Integrations**.

Tell your community that messages in those channels are analyzed. Seismograph
cannot do that for you.

## Environment variables

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | yes | — | Bot token. |
| `DISCORD_GUILD_ID` | yes | — | The single guild to operate in. |
| `SOURCE_CHANNEL_IDS` | yes | — | Comma-separated allowlist of channels to read. |
| `REPORT_CHANNEL_ID` | yes | — | Channel that reports are posted to. |
| `LLM_API_KEY` | yes | — | Key for the chat-completions endpoint. |
| `LLM_BASE_URL` | yes | — | Base URL, e.g. `https://api.example.com/v1`. |
| `LLM_MODEL` | yes | — | Model name passed to the endpoint. |
| `AUTHOR_HASH_SALT` | yes | — | HMAC key for author pseudonyms, 16+ characters. |
| `REPORT_TIMEZONE` | no | `UTC` | IANA timezone used for period boundaries. |
| `ANALYSIS_DAYS` | no | `7` | Length of the analysis window in days. |
| `RETENTION_DAYS` | no | `30` | How long stored messages are kept. |
| `DATABASE_PATH` | no | `seismograph.db` | SQLite file location. |

Copy `.env.example` and fill it in. Never commit a real `.env`. Missing or
unusable configuration is reported in full at startup and the process exits.

## Running

```bash
python -m seismograph run       # start the bot
python -m seismograph period    # print the current analysis window
python -m seismograph prune     # delete messages past the retention window
```

The weekly report is checked hourly and fires on Monday at 09:00 in
`REPORT_TIMEZONE`. Completed scheduled periods are recorded, so a restart inside
that hour cannot produce a second report for the same period.

### Docker

```bash
docker build -t seismograph .
docker run --rm --env-file .env -v seismograph-data:/data seismograph
```

The image runs as a non-root user and expects `DATABASE_PATH=/data/seismograph.db`
with a volume mounted at `/data`, otherwise the database is lost when the
container is replaced. Secrets are supplied at run time and are not baked in.

## LLM provider

Any endpoint that accepts an OpenAI-compatible
`POST {LLM_BASE_URL}/chat/completions` with a bearer token works. Set
`LLM_BASE_URL` and `LLM_MODEL` to point at a hosted provider, a gateway, or a
local server. Requests are sent with `temperature: 0` and
`response_format: {"type": "json_object"}`; a provider that ignores
`response_format` still works as long as it returns a JSON object, since output is
parsed and validated locally either way. No provider is referenced in the domain
logic.

## Privacy

- Only allowlisted channels in the configured guild are read. Direct messages are
  never accessed.
- Author ids are replaced with `HMAC-SHA256(AUTHOR_HASH_SALT, author_id)`
  truncated to 32 hex characters, before storage. Usernames, nicknames, avatars,
  and raw author ids are never stored. Author hashes never appear in a report.
- Message content is redacted before it is stored or sent to the model: email
  addresses, phone numbers, API-key-shaped strings, bearer tokens, long
  high-entropy strings, IPv4 addresses, user mentions, and invite links are
  replaced with placeholders. This is a best-effort filter, not a guarantee.
- Reports link to original messages rather than reproducing their text.
- Stored data is limited to what a report needs: message id, channel id, author
  hash, timestamp, redacted content, plus analysis runs, signals, evidence links,
  and reaction feedback.
- `RETENTION_DAYS` bounds message retention and `python -m seismograph prune`
  applies it. The scheduled weekly run prunes automatically.
- Logs record counts, periods, and errors. Tokens, keys, salts, full environment
  variables, and raw message bodies are never logged.

Server administrators remain responsible for disclosure, consent, and compliance
in their jurisdiction. This software does not provide or guarantee legal
compliance.

## Tests and linting

```bash
pytest
ruff check .
ruff format --check .
```

Tests cover the deterministic domain: scoring bounds and monotonicity, loudness
versus breadth, emerging detection, missing history, evidence validation,
rejection of invented message ids, jump-link construction, author hashing,
redaction, configuration failures, empty analysis, the single repair attempt, and
prompt-injection text remaining data. Discord library calls are not mocked.

## Known limitations

- One guild per process. Multi-guild deployments need separate processes.
- Signals are matched across runs by normalized title and category. A reworded
  title is treated as new, which makes the growth component conservative.
- Clustering happens inside the model. There are no embeddings, so wording that
  differs sharply may split into separate signals.
- Batch merging is a single extra model call with a deterministic title-based
  fallback; very large weeks may still produce near-duplicate signals.
- The weekly schedule depends on process uptime. A process that is down for the
  whole scheduled hour skips that week; run the command manually to recover.
- Reaction feedback is recorded per report, not per signal, and nothing consumes
  it yet.
- Content redaction is regex-based and will not catch every sensitive string.
- Attachments, embeds, threads, forum posts, and edits after collection are not
  analyzed.
- Reports are plain Markdown messages rather than rich embeds.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Vulnerability reports go through the
process in [SECURITY.md](SECURITY.md), not public issues.

## License

MIT. See [LICENSE](LICENSE).
