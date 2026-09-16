# Case changes: evidence versus interpretation

`/seismograph_changes` compares the latest two retained revisions of an
investigation. It is a staff-requested, deterministic read of existing data:
no model call, history fetch, public post, or background monitoring is added.

The purpose is to answer a practical question: did the evidence change, or
did the model's interpretation of the same evidence change?

## Use in Discord

```text
/seismograph_changes case_id:12
```

The command requires administrator permission at execution time, runs only in
the configured guild, checks source/report permissions through the existing
preflight, and sends an ephemeral response with mentions and embeds disabled.
Both revisions must use only currently allowlisted sources, including context
messages the model did not cite. It shares the existing report lock and refuses
to run while another report or investigation is active.

At least two retained revisions are required. A normal case refresh creates
another revision through the existing investigation workflow; the comparison
itself does not create one or call a model. If only one revision exists, or
privacy invalidation removed the history, the command explains that it cannot
compare. It does not silently substitute an unrelated or older case.

## What it shows

- **Selected context:** Message IDs newly included or no longer included in the
  bounded evidence window. Newly selected does not necessarily mean newly posted.
- **Observations:** Added, omitted, and rewritten observations, with before/after
  outcomes, conditions, exact excerpts, and source links.
- **Promoted evidence:** Newly cited observations from messages that were already
  available in the earlier context.
- **Interpretation changes:** Changes to the case title, hypothesis text or
  references, and proposed follow-up question. Observation order alone is ignored.
- **Follow-up counts:** Distinct failure and non-conflicting success reporters
  for each selected snapshot under the current marker.
- **Lost failure evidence:** A caution when earlier post-marker failure
  observations are omitted or relabeled. An improved displayed status does not
  establish that those reporters recovered.

It distinguishes omission by the model from removal of a source from the selected
window. A message outside the latest window is not necessarily deleted, and a
different interpretation is not necessarily a changed real-world condition.
The card shows up to eight changed observations; aggregate counts cover all
changes in the bounded pair. Each revision is limited to 120 retained inputs.

## What it does not establish

The current schema stores one replaceable intervention marker, not a historical
marker for every revision. Both snapshots are therefore recomputed against the
**current marker**. These comparisons must not be presented as the statuses
staff actually saw at historical times.

The change detector compares structure and text, not semantic truth. It cannot
tell whether a reclassification was correct, whether a report was retracted,
or which reporter actually received a patch. It does not change the existing
verification status, suppress a warning automatically, establish causality,
or certify resolution. The earlier frozen investigation prompt and counting
rules remain unchanged.

## Privacy and storage

No schema migration, new persistent table, provider request, or runtime
dependency is introduced. The comparison reads only the two newest revisions
and their bounded source contexts in one SQLite read snapshot.

Existing edit, deletion, opt-out, and retention invalidation removes the
affected case's retained revision history. The comparison cannot resurrect it.
Before each Discord chunk is sent, the command checks that the two revision IDs
and current marker are still the same; subsequent chunks stop after a change.
This is not an atomic transaction with Discord: an in-flight send or a response
already delivered cannot be recalled by the local check. Previously exported
files and sent messages remain an operator cleanup responsibility.

## Try without any connection

```sh
python -m seismograph changes-demo
```

The fictional replay runs real validation, persistence, comparison, rendering,
and opt-out handling with recorded interpretations:

- Two explicit follow-up successes produce corroborated improvement.
- A later failure changes the selected-evidence status.
- A subsequent model output omits that failure despite retaining the source
  context; the change log flags the apparent recovery.
- An opt-out invalidates the history and makes further comparison unavailable.

This demonstrates code behavior, not a real model's accuracy or production
Discord behavior. See the [recorded demo](case-changes-demo.md) and
[validation record](validation.md) for the tested scope.
