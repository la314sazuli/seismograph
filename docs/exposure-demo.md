# Seismograph Patch Exposure Demo

Fictional sources and recorded outcomes; no Sonar or Discord connection.
Staff assessments are interpretations of quoted reports, not verified installation.
The opaque scope key and audit times are generated during the replay.

## A release timestamp is not exposure

All three post-marker reports initially remain unknown, even when they name a build.
There is no automatic rollout assumption or model-generated exposure label.

## Three different follow-up groups

# Case 1 patch exposure
Revision 1; scope_key: `84f12cc7c46f3d12a7abf1b53d2cb2455913bfbe4ab89736874e699d3312c1b1`.
Release: Fictional build 42 PDF patch at 2026-08-05T12:00:00+00:00.
Staff interpretation of quoted reports, not verified patch installation.
Post-marker timestamps alone never establish exposure.

## Original model outcomes
- received: 1 observations; 1 reporters; 0 failure / 1 success reporters without a conflicting failure in this group.
- not_received: 1 observations; 1 reporters; 1 failure / 0 success reporters without a conflicting failure in this group.
- unknown: 1 observations; 1 reporters; 1 failure / 0 success reporters without a conflicting failure in this group.
Unique post-marker reporters: 3; reporters across multiple exposure groups: 0.
Excluded by staff: 0; at/before marker: 4 observations.

## Staff-adjusted outcome preview
- received: 1 observations; 1 reporters; 0 failure / 1 success reporters without a conflicting failure in this group.
- not_received: 1 observations; 1 reporters; 1 failure / 0 success reporters without a conflicting failure in this group.
- unknown: 1 observations; 1 reporters; 1 failure / 0 success reporters without a conflicting failure in this group.
Unique post-marker reporters: 3; reporters across multiple exposure groups: 0.
Excluded by staff: 0; at/before marker: 4 observations.

Unknown-exposure failures remain unresolved evidence, not proof of patch failure or recovery.
Reporter groups can overlap; do not add them or infer a rollout or success rate.
No overall resolution status or causal claim is derived from these segments.

## Exposure assessment history
Current release only. Page 1 of 1; up to 8 entries.
- Assessment 1 (current): received, 2026-09-16T23:46:23+00:00.
  https://discord.com/channels/100000000000000001/200000000000000011/300000000000000005
  Exposure excerpt: I installed build 42 with the PDF patch. Exported citations now work.
  Reason: The reporter explicitly names the installed build.
- Assessment 2 (current): not_received, 2026-09-16T23:46:23+00:00.
  https://discord.com/channels/100000000000000001/200000000000000011/300000000000000006
  Exposure excerpt: I am still on build 41 without the patch. PDF citations are missing.
  Reason: The reporter explicitly names the installed build.

A new revision or release resets exposure to unknown. No model call was made.
Edits, deletion, retention, and reviewer opt-out can erase this retained audit.

## Retract an assessment without hiding its history

The received assessment is superseded by an explicit unknown reset.
Both reasons remain in the bounded audit; this is not evidence of recovery.

## Reviewer opt-out does not restore an earlier claim

The entire affected observation's exposure ledger is erased, including the
superseded received entry. The unrelated not-received assessment remains.

## Re-recording a release starts a new scope

Even the same release note and timestamp produce a new key. All exposure
returns to unknown; old commands cannot silently apply to the new marker.

## Source deletion

Removing contributing evidence deletes dependent revisions and exposure audits.
Already-exported files and delivered or in-flight Discord text still require
operator cleanup. This synthetic replay is not a live-model or capacity result.
