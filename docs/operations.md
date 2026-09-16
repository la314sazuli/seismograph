# Large-community rollout

Seismograph runs one process for one guild, with no member-list downloads or
member cache. A guild's membership is not a throughput measurement: size the
installation for messages in the selected channels, their text length, provider
latency, and analysis quality. This project has not been validated against a live
500,000-member guild.

## Before enabling collection

When upgrading an existing installation, stop the old process and take a
consistent database backup first. The current release migrates schema v1–v4 to v5;
rollback to an older binary requires the pre-upgrade backup. See
[staff review](staff-review.md) and [patch exposure](patch-exposure.md) for the
retained data and deletion behavior. Exposure judgments require exact quotes
and reset to unknown on every new revision or release marker.

Report delivery revalidates its source snapshot between message chunks. A source
edit, deletion, or opt-out stops the remaining chunks and leaves a partial send
marked `uncertain`, so the scheduler does not automatically repost it.
Already-sent or in-flight text is not recalled and requires operator cleanup.

- Obtain the server owner's approval for the specific feedback channels. Avoid
  general chat and channels likely to contain sensitive support or account data.
- Disclose what is collected, the model provider, retention, and how to opt out.
  Provide a private operator contact for data deletion and incident reports.
- Review the provider's retention, training, region, and subprocessors. Require
  no training on submitted content; prefer a controlled local model or suitable
  contractual data protections. Regex redaction does not make arbitrary messages
  anonymous or safe to share.
- Review the [Discord Developer Policy](https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy).
  It prohibits training models on API message content without express permission,
  restricts data use to the application's functionality, and requires respecting
  applicable opt-outs. Do not repurpose this application into a general scraper.
- Enable Message Content Intent in the Developer Portal and obtain any required
  review. The [Gateway documentation](https://discord.com/developers/docs/events/gateway)
  describes privileged-intent review, including a 10,000-visible-user threshold;
  do not assume that installing in only one large guild avoids review. Confirm
  the requirements shown for your application in the portal before rollout.
- Set `LLM_PROCESSING_APPROVED=true` only after these checks. This flag records
  an operator decision, not a legal-compliance guarantee or Discord approval.

## Channel permissions

Use `bot` and `applications.commands` installation scopes. Do not grant
Administrator, Server Members Intent, Presence Intent, or access to unrelated
channels. Source channels need View Channel and Read Message History.

Use a staff-only text channel for reports, granting the bot View Channel, Read
Message History, Send Messages, and Add Reactions. Deny View Channel to
`@everyone`; inspect other role overwrites yourself. The preflight rejects
destinations visible to `@everyone`, but cannot identify which of your custom
roles genuinely represent staff.

Allowlist text channels and individual public thread IDs explicitly. Threads do
not inherit a parent's allowlist entry; forum containers, automatic thread
discovery, and private threads are unsupported. Archived public threads may be
read by their explicit ID when the API allows it.

Reports can reveal information from several source channels. Every staff member
who can read the report should be authorized to see the combined source content,
even if Discord independently restricts the original jump links.

## Start a pilot

1. Run the synthetic demo and test suite.
2. Configure one or two disclosed feedback channels and a private staff channel.
3. Choose a model supporting JSON chat completions and an adequate context
   window. Prompts use about 24,000 message characters plus system instructions;
   output is capped at 8,192 tokens. Characters are not tokens, especially for
   non-English content. Validate the actual provider/model before increasing load.
4. Set the environment variables, then start the bot. On startup it catches up
   the latest scheduled period, not every missed week. This can begin model
   processing immediately after login and preflight.
5. Inspect `/seismograph` output with several staff reviewers. Check original
   messages for false merges, missed signals, unsupported claims, identity
   leakage, and severity calibration. Confidence is model-estimated, not a
   probability that a bug exists.
6. Keep initial limits small. Review request counts, provider costs, report
   quality, Discord errors, memory, and runtime before expanding the allowlist.
7. Run at least two comparable weekly periods before relying on trend labels.

No synthetic benchmark proves live clustering accuracy or Discord throughput.
Do not announce full-production readiness solely because tests pass.

## Bounds and failure behavior

`MAX_MESSAGES=20000` caps total fetched history messages per report across all
sources, including bots and noise. The stored analysis query has the same cap.
History writes at most 100 rows per transaction. Exceeding the cap stops the
report rather than silently sampling, omitting channels, or claiming complete
coverage. Reads use the library's API rate-limit handling, not parallel scraping.

`MAX_LLM_REQUESTS=200` caps HTTP attempts for each report, including transient
retries and repairs. Network failures and selected transient statuses get at
most three attempts with bounded waits; permanent failures stop immediately.
Model work runs in a worker thread with its own SQLite connection, leaving the
Discord event loop available. Only one report runs at a time.

Every analysis response gets at most one schema repair; an invalid batch or
merge stops the whole report. Identical-title/category candidates merge locally.
Other candidates are grouped in batches of at most 20 descriptors; the model
returns candidate indices, while Python unions evidence IDs and recounts users.
Near-duplicates across merge-batch boundaries may remain. No embedding database
or unbounded final prompt is used.

Reports show up to 10 main signals, 3 emerging signals, 3 positive signals, and
3 unranked review signals. Counts are supporting-message counts, not estimated
community prevalence. Selected-channel observations are not representative of
all members, and unique pseudonyms are not verified unique people.

## Schedule and recovery

`REPORT_WEEKDAY` uses Monday=0 through Sunday=6. `REPORT_HOUR` is an integer local
hour in `REPORT_TIMEZONE`. Every five minutes, the scheduler checks the latest
due weekly window. Its identity remains stable through restarts and late starts.
Retain at least `ANALYSIS_DAYS + 7` days to cover the catch-up window.

Run `python -m seismograph runs` to inspect the latest 20 records:

- `analyzing`: claimed before collection/analysis. After a process crash, inspect
  it before any manual retry; there is no automatic stale-claim takeover.
- `failed`: collection or analysis failed before any send attempt.
- `empty`: valid analysis produced no sufficiently supported report.
- `sending`: send intent was persisted before contacting Discord.
- `uncertain`: delivery may have happened or only some chunks reached Discord.
- `published`: all report chunks were sent; reaction failures do not undo this.

For a pre-publication failure, `python -m seismograph runs --retry ID` clears only
a `failed` scheduled run. The next tick retries if that period is still the
latest due period. Earlier weeks are not automatically replayed.

Never automatically clear `sending` or `uncertain`. Inspect the staff channel
and operator logs first, remove partial messages if appropriate, and deliberately
request a new manual report. Discord sends and SQLite commits cannot form one
atomic transaction; this application favors no automatic duplicate over
guaranteed delivery. A manual report is a new request and can repeat a period.

Reaction feedback is report-level, collected from administrators on the first
message, and not used to change ranking. Individual reaction removal is reflected;
bulk reaction clears and changes while offline are not reconciled.

## Privacy and storage

Content is regex-redacted before storage and again before analysis. Report text
is redacted again and mentions are disabled, but names and sensitive free-form
facts can still survive. Author HMACs are pseudonyms, not irreversible anonymity.
Treat the database, backups, and generated reports as sensitive.

`/seismograph_optout` removes that user's local messages, affected signal records,
and feedback, then retains only the pseudonym needed to suppress re-ingestion.
That suppression entry intentionally survives retention. Do not rotate the
author secret casually: rotation breaks opt-outs and history linkage.

Gateway edits/deletes invalidate locally derived signals. Successful bounded
history scans reconcile deletions made while offline. An edit, deletion, or
opt-out detected during analysis cancels that report before publication.
Already-posted Discord reports and provider-held copies are not automatically
erased; staff must handle these separately. Changes arriving after final
validation can still race publication.

Maintenance prunes messages, expired runs, signals, evidence, and feedback while
no report is active. `python -m seismograph prune` also works without Discord or
model credentials. Long runs can delay automatic pruning. Secure-delete and WAL
checkpointing reduce residual local data but do not erase backups or guarantee
forensic erasure on SSDs.

Use one Linux/macOS process and one local database volume. The run command takes
an exclusive file lock; multiple replicas and network filesystems are unsupported.
Back up the database before upgrading: schemas v1 and v2 migrate to v3 in place and an
older binary cannot read the upgraded database. Stop the bot before taking a
simple filesystem backup, and apply the same retention/access policy to backups.

## Local load check

```bash
python tools/benchmark.py --messages 20000
python tools/benchmark.py --messages 100000
```

The script creates fictional messages, writes SQLite batches, runs actual
preprocessing, validation, evidence union, scoring, and report rendering using
a deterministic recorded classifier, then deletes the temporary database.
It reports elapsed time and Linux peak RSS. It does not connect to Discord or
a model provider and does not measure semantic quality, network latency, or
real API throughput.
