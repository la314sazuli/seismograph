# Patch exposure

Posting after a release does not prove that someone received the patch.
Seismograph separates post-marker observations into `received`, `not_received`,
and `unknown`, using explicit staff assessments with exact source excerpts.
This is report interpretation, not installation telemetry, rollout measurement,
or an automatic success verdict.

## Run it offline

```bash
python -m seismograph exposure-demo
```

No credentials or network are needed after installation. The fictional replay
uses the real storage, assessment, counting, privacy, and rendering code.
It covers a successful report on a patched build, a failure on an unpatched
build, and a failure with unknown exposure. Read the
[recorded replay](exposure-demo.md) without installing anything.

## Private staff workflow

Both commands require administrator permission at runtime, run only in the
configured guild, and return ephemeral messages with mentions disabled.
They share the report lock and source-permission preflight. Neither calls a model,
publishes to source channels, contacts reporters, nor changes case status.

1. Record a release using `/seismograph_release case_id note`. Use a precise
   patch/build description rather than a vague “fixed.”
2. Refresh the investigation to collect follow-up evidence when live collection
   is available, or use an existing revision containing post-marker observations.
3. Read `/seismograph_exposures case_id page:1`. Copy its `scope_key`, a 64-character
   revision-and-release identifier. This is a freshness guard, not a password.
4. Use `/seismograph_exposure case_id scope_key message_id state reason quote`.
   Choose an observation in the current revision, strictly after the marker.
   Copy an exact 8–350-character excerpt from that observation's retained,
   redacted source. Explain why it supports exposure to this specific patch.
5. Read the original and staff-adjusted outcome groups separately. Use
   `/seismograph_reviews` to inspect any outcome corrections.

For `received` and `not_received`, `quote` is mandatory. For `unknown`, omit
`quote`; this appends a reset with a reason rather than deleting prior entries.
Reasons must be 8–300 characters before best-effort redaction. Repeating the
current state is rejected. To revise a same-state rationale, first reset to
unknown and then add the replacement assessment, retaining both reasons.

Examples of supporting excerpts are “I installed build 42 with the PDF patch”
and “I am still on build 41 without the patch.” A timestamp, generic success,
or “after the patch” alone is not enough to confidently identify the installed
build. Staff must judge relevance; exact-match validation prevents fabricated
excerpts but cannot establish that a claim is true or that the label follows
from it. There is deliberately no model-based exposure inference.

## How counts work

- **Unknown by default:** Every selected post-marker observation starts unknown,
  including messages mentioning a build, until staff assess that observation.
  Exposure never propagates to other messages by the same reporter.
- **Original versus adjusted:** One panel retains model outcome labels. The
  other applies active staff corrections and excludes observations explicitly
  marked `exclude`. Neither rewrites the saved model response.
- **Reporter conflicts:** Within each exposure group, a failure takes precedence
  over a success from the same reporter. A reporter can appear in several groups;
  the view shows both cross-group overlap and a deduplicated total.
- **No invented denominator:** These are bounded selected observations, not all
  server members, all patch recipients, or all affected users. Do not add group
  reporter counts or interpret them as a rollout or success rate.
- **Unknown failures remain evidence:** An unknown-exposure failure proves
  neither failure of the patch nor recovery. An unpatched failure is separated
  from patched reports but is not silently discarded from the original case.

The normal case and changes cards show a compact exposure warning whenever a
release marker exists. Original verification counts and conservative statuses
remain unchanged; segmentation does not automatically resolve a case.

## Scope and bounds

Every entry belongs to one exact revision and one exact release-marker key.
A refresh creates a new revision with all exposure unknown. Recording a new
marker, even with identical text and time, also resets exposure to unknown.
Returning to an earlier marker's text does not restore earlier assessments.
Stale commands fail instead of applying to a different scope.

The audit allows at most 120 entries per revision across all release markers,
including resets and superseded entries. Its current-marker view is paginated
at eight entries per page. Entries from replaced markers are retained in SQLite
until their revision is invalidated, but are not applied or shown as current.
There is no cross-release history browser. A refresh provides a new review scope,
not a claim that the same evidence is new.

Replies recheck revision, release, correction audit, and exposure audit before
each Discord chunk. A concurrent change stops remaining chunks. Already-sent
or in-flight messages cannot be recalled by this guard.

## Storage, deletion, and upgrade

Schema v5 adds a random marker identifier and an exposure audit containing the
revision, marker key, message ID, state, exact redacted excerpt, redacted reason,
pseudonymous reviewer hash, and creation time. It adds no runtime dependency,
external service, provider requirement, or configuration secret.

Deleting, editing, opting out, or expiring a contributing source invalidates
dependent case revisions and cascades to their exposure audits. The rule also
applies to contributing context not selected as a displayed observation.

Reviewer opt-out erases the entire exposure ledger for each revision/observation
pair in which that reviewer participated, across markers and including other
reviewers' entries in that ledger. This conservative deletion prevents an older
assessment from becoming current again when a later reset is erased. Unrelated
observations remain. Privacy deletion takes precedence over audit completeness;
this is not an immutable compliance log. Exported files, provider-held data,
backups, and delivered Discord messages require separate operator handling.

Stop the process and take a consistent SQLite backup before upgrading.
Schemas v1–v4 migrate to v5, preserving retained cases and staff corrections.
Existing release markers get fresh keys; old observations start unknown.
Reopening the database does not rotate keys. Older binaries reject the newer
schema, so rollback requires the pre-upgrade backup, not just an older image.

This feature is exercised with fictional reports and offline contract tests.
It has not been validated with live Sonar responses, Discord gateway traffic,
or a 500,000-member production server.
