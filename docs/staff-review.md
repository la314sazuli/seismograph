# Staff corrections without rewritten evidence

An exact quote can still receive the wrong outcome label. Staff review records
a human correction beside the original model interpretation and shows a
separate preview of its effect on follow-up counts. Neither the source message,
the stored model response, nor the original case status is overwritten.

## Operator workflow

Read the case, then inspect the current review revision:

```text
/seismograph_case case_id:12
/seismograph_reviews case_id:12
```

Copy the opaque `revision_key` displayed by the review command. It is a freshness
guard, not a password. Unlike a numeric SQLite revision ID, it is not reused
after deletion. Choose a message ID from an existing observation on that exact
revision:

```text
/seismograph_correct case_id:12 revision_key:<copied-key> message_id:<observation-id> outcome:failure reason:"The excerpt describes a failed export, not a successful one."
```

Supported outcomes are `failure`, `success`, `counterexample`, `workaround`,
`unclear`, and `exclude`. Exclusion removes the observation only from the
staff-adjusted preview. It does not delete the message or the model observation.
Reasons must be 8–300 characters. Do not copy personal details or unrelated
messages into them; automatic redaction only catches obvious identifiers.

There can be one active correction per observation. Replacing it requires
withdrawal with a reason, then a new correction:

```text
/seismograph_withdraw case_id:12 revision_key:<copied-key> correction_id:3 reason:"Withdrawing after checking the original workflow."
/seismograph_reviews case_id:12 page:2
```

Any currently authorized administrator can withdraw a current-revision
correction, not just its original reviewer. Both reasons and timestamps remain
in the retained audit. Reviewer IDs are stored only as salt-dependent HMAC
pseudonyms and are not displayed or sent to a model. This is not a named
approval workflow or a tamper-evident compliance log.

## What the preview means

The original model-selected counts and staff-adjusted preview are displayed
separately. Both use the current intervention marker and the existing distinct
reporter rules. A preview can change counts by relabeling or excluding selected
observations; it is not new evidence of improvement or failure.

The normal case and change-log commands display a staff-review warning while
the latest revision has active corrections. Their original counts remain
unchanged. Hypotheses, quotations, conditions, and follow-up questions are not
rewritten. Staff notes do not enter the investigation prompt.

Corrections cannot add an observation the model omitted, alter source text,
establish patch exposure, infer causality, or certify resolution. Staff can be
wrong too. An apparent improvement caused by excluding a failure remains a
human interpretation, not proof of recovery.

## Revision and concurrency boundaries

Corrections are attached to one exact retained revision. A refresh creates a
new key and does not inherit corrections, even when the model repeats the same
label. An old key is rejected. Corrections on older retained revisions remain
stored but are inactive; the current commands expose only the latest revision,
not a full historical-audit browser.

All three review commands require runtime administrator permission, the
configured guild, and the existing source/report permission preflight. Replies
are private, disable mentions and embeds, and share the report lock. Writes
recheck the revision, full source allowlist, and reviewer opt-out inside a
SQLite writer transaction.

Each revision is bounded to 120 source messages and 120 correction entries,
including withdrawals' parent corrections. The audit is paginated in groups
of eight. Reaching the limit refuses new corrections rather than pruning
the audit silently.

Before each reply chunk, the code checks the revision key, current marker,
and review state again. A concurrent edit, deletion, opt-out, correction,
withdrawal, or refresh stops later chunks. This is not atomic with a Discord
send; already-delivered or in-flight text cannot be recalled. If a send fails
after a write, read the review before retrying: the correction may already exist.

## Privacy and retention

Existing source edits, deletions, opt-outs, and retention invalidate affected
case revisions. Foreign-key cascades remove their corrections and withdrawals,
including those attached to older revisions. Reviewer opt-out removes that
reviewer's corrections. If the reviewer authored a withdrawal, its parent
correction is also deleted so removing the withdrawal cannot reactivate it.

An opted-out administrator cannot write further review records. The local
suppression hash remains under the existing opt-out policy. No raw Discord
reviewer IDs or names are stored, but HMAC pseudonyms remain personal data for
operational purposes. Redaction is best effort; administrators must keep notes
free of unnecessary personal information.

Review records expire with their source-derived revision history, not on an
independent review-age timer. Privacy erasure takes precedence over audit
continuity. Backups, exports, already-posted messages, and provider-held data
still require separate operator handling.

## Database upgrade and offline demo

Schema v4 adds a revision freshness key, correction and withdrawal tables,
indexes, and privacy triggers. Startup migrates v1–v3 databases automatically;
model payloads and evidence remain unchanged. Stop the old instance and take a
consistent backup before upgrading. Older binaries cannot open v4; rollback
requires restoring the pre-upgrade backup, not downgrading the live database.

```sh
python -m seismograph review-demo
```

This fictional replay intentionally labels an explicit failure as success,
then shows a staff correction, its withdrawal, reviewer opt-out, and source
deletion. It exercises real persistence and preview logic, not a real model.
No Discord or Sonar access is needed. See the [demo](staff-review-demo.md)
and [validation record](validation.md). No new runtime dependency is added.
