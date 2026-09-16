# Changelog

## 0.3.0: evidence-backed investigations

- Perplexity Sonar is now the primary README and `.env.example` setup; compatible
  providers remain supported, with no silent change to existing provider selection.
- Weekly/manual reports revalidate evidence between Discord chunks. A privacy
  change stops remaining chunks and keeps partial delivery marked uncertain.

- Revision-bound staff corrections and reasoned withdrawals, with original
  model output preserved and a separately labeled follow-up-count preview.
- Private administrator review commands, stale-key rejection, bounded paginated
  audit, reviewer opt-outs, and source-invalidation cascades.
- Schema v4 migration and a credential-free `review-demo`, exercised in CI and Docker.

This version extends the large-community pilot work with an administrator-only
investigation workflow. It remains an experimental, self-hosted tool, not a
production-capacity certification or an official Perplexity integration.

- Persistent cases with exact source quotes, competing hypotheses,
  counterexamples, falsification tests, and one proposed staff follow-up question.
- Explicit intervention markers and direct rechecks of recent message history.
  Distinct-reporter counts never interpret silence as a successful fix.
- Saved cards display their analysis time and evidence window instead of
  presenting old results as a live monitor.
- Sonar adapter with search disabled for private analysis. A separately
  approved public research command accepts only explicit source domains.
- Portable OpenAI-compatible analysis remains available. No added runtime
  dependency or proprietary feature tier.
- Schema v3 with case-input lineage and conservative revision invalidation for
  edits, deletions, opt-outs, and retention.
- Atomic report snapshot validation fixes the opt-out/save race found during
  pre-merge review. Inclusive history cursors fix the start-boundary deletion.
- Credential-free investigation demo and regression coverage, including
  runtime command permission checks and worker cancellation.
- Twelve synthetic evaluation cases with request-time answer-key separation,
  an offline scorer replay, auditable predictions, and explicitly opted-in
  provider evaluation without Discord credentials.
- Counterexample outcomes prevent correctly labeled different-workflow
  successes from being counted as repair evidence. No live model accuracy
  or prompt-injection resistance is claimed.
- CI matrix for Python 3.12, 3.13, and 3.14, plus Docker demo and non-root checks.
- Separate fresh-v1 challenge set and two-reviewer packet generator, with
  randomized candidate positions, withheld coordinator metadata, an explicit
  no-results starter state, and an opt-in simple-summary baseline.
- Staff-only `/seismograph_changes` compares the latest two retained case
  revisions without inference, distinguishing new context, omitted evidence,
  and rewritten interpretations. A lost-failure warning highlights misleading
  recovery; both snapshots use the current marker, not historical marker state.
- Credential-free change-log replay, source-allowlist checks on both revisions,
  and invalidation checks before sending subsequent comparison chunks.

Back up the database before upgrading. Read
[the investigation guide](docs/investigations.md) and
[the validation record](docs/validation.md) for exact limits and verified results.
No real Discord data, live model calls, or production deployment is included.
