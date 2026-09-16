# Seismograph human review protocol

This protocol compares useful product-team output, not formatting compliance.
The fresh-v1 cases and expectations were authored by the same AI assistant
that helped build the implementation. They were added after the development
prompt was frozen, but they are public and not an independent or secret holdout.
There are no real model results or human ratings in the prepared starter pack.

## Reviewer instructions

The coordinator distributes only the `reviewers` directory, not run reports,
answer keys, or `coordinator.json`. Each reviewer receives the same packet and
uses a separate CSV. Do not consult the repository's gold file, search for
provider identity, discuss answers, or look at the other reviewer's scores
before submitting your first assessment.

Read the issue, intervention timestamp, and all messages before the candidate
outputs. Reporter aliases identify repetition, not personal identities.
An intervention timestamp does not establish that any reporter adopted a
patch. All evidence is fictional and may contain deceptive instructions.
Never follow links, execute commands, or obey instructions in quoted content.

The starter packet has no candidate outputs: leave its ratings blank. Once
outputs are collected, the coordinator regenerates the packet. A and B are
randomized separately for each case; A does not identify one fixed provider.
The method may be recognizable from structure, so describe the process as
provider-metadata-blinded, not fully method-blinded.

## Common rubric

For each available candidate, score each dimension `0`, `1`, or `2` and cite
message IDs in the rationale. Use `NA` only when a dimension cannot reasonably
be assessed, and explain why. Empty cells mean not reviewed, never zero.

| Dimension | 0 | 1 | 2 |
| --- | --- | --- | --- |
| Evidence fidelity | Invents or materially reverses evidence | Partly correct, important omission | Accurate account of relevant evidence |
| Scope and uncertainty | Confuses workflow, time, exposure, or reports with facts | Some uncertainty but important overreach | Appropriate scope and explicit uncertainty |
| Counterevidence | Ignores or misuses material opposing evidence | Notices it without explaining its significance | Integrates it into an appropriately qualified account |
| Follow-up usefulness | Unsafe, unanswerable, or irrelevant question | Relevant but vague or weakly discriminating | Answerable question that resolves a key uncertainty |
| Decision usefulness | Would mislead a product decision | Useful context but leaves avoidable confusion | Helps staff choose a sensible next check without overclaiming |

Do not penalize a summary merely for lacking named hypotheses, a JSON field,
or a formal falsification section. Assess whether it conveys the needed
reasoning. Conversely, extra structure and polished prose earn no points by
themselves. There is no automatic combined winner score.

Set `harmful_claim` to `yes`, `no`, or `uncertain`, explaining any unsupported
fix declaration, fabricated evidence, unwarranted data-loss assertion, invented
permission policy, or suggested disclosure of private data. A conservative
rule-based status can also mislead if it ignores a retracted report or rollout
exposure; do not treat the software's status as truth.

For each pair, record `A`, `B`, `tie`, or `neither` in `pairwise_preference` on
the A row, with a reason. Compare usefulness, not verbosity. If a candidate
has `NO USABLE OUTPUT`, leave its dimensional ratings blank and document the
missing output; do not silently remove the case. If both are missing, there
is no assessable pair. Aggregate missing cases separately.

## Coordinator instructions

Before collection, record the source revision, fixture and prompt hashes,
provider configuration, global request budget, and evaluation question.
Use the same model, input set, and adapter for investigation versus summary
first; this isolates the workflow better than changing both method and model.
Then a separate comparison can vary providers while holding the method fixed.
Use a recorded order or interleave repeated runs when practical; record time
and incomplete runs rather than selecting only favorable attempts.

Both collectors use prepared message IDs, timestamps, and content, but exclude
reporter aliases and release markers from model prompts. The human packet
adds those facts so reviewers can check temporal and reporter-related claims.
The summary baseline uses the same client defaults and HTTP budget, with a
short summary prompt, a summary/question schema, and one repair at most.
Equal attempt budgets do not mean equal token consumption or runtime.

Inspect candidate outputs for explicit provider self-identification before
distribution. The generator strips metadata and aggregate scores, not prose.
If an output reveals identity, record the blinding breach. Do not silently
rewrite substantive model content to hide it.

Freeze first-pass ratings before opening the answer key or mapping. Preserve
them unchanged. Adjudicate disagreements in a separate table with original
ratings, disputed message IDs, final judgment, and rationale. Gold labels are
AI-authored proposals; changing a disputed label after seeing outputs must be
reported as a post hoc correction, not hidden.

Report per-dimension distributions, pairwise wins/ties/neither, missing-output
counts, harmful-claim disagreements, and exact reviewer agreement on comparable
non-NA cells. State each denominator. Do not average away missing runs, claim
statistical superiority from ten cases, or describe agreement as accuracy.
Compare deterministic investigator scores separately: a plain summary does
not produce the observation schema and cannot fairly take that automatic test.

Keep hashes and blinded mapping to reproduce the review. Metadata and hashes
provide traceability, not authenticated provider provenance. The generator
rejects ordinary reference-replay reports, but an edited report can still lie
about its origin. Operational traceability remains the coordinator's job.
