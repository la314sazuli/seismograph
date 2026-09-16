# Local validation

Measured on 2026-09-16 in a Linux sandbox with Python 3.12, 2 vCPUs and 8 GiB RAM.
These are synthetic engineering checks, not production capacity or model-quality
results.

| Check | Result |
| --- | --- |
| `pytest` | 157 passed; one upstream `audioop` deprecation warning |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed |
| `python -m seismograph demo` | Passed without credentials or network calls |
| 20,000 synthetic messages | 0.612 seconds total; 72.4 MiB peak RSS; 92 recorded-classifier batches |
| 100,000 synthetic messages | 5.104 seconds total; 162.6 MiB peak RSS; 459 recorded-classifier batches |

The benchmark exercises SQLite ingestion, preprocessing, validation,
evidence-preserving title merging, measured author counts, scoring, and rendering.
Every synthetic message describes the same issue, so this is not a diverse-topic
clustering benchmark. The 100,000-message run deliberately overrides the default
message limit and substitutes a classifier with no HTTP calls; it would exceed
the default 200-request allowance with a live model.

The unit suite separately checks bounded multi-topic merge groups, invalid
responses, retries, event-loop responsiveness, scope isolation, opt-outs, schema
migration, scheduling, changed evidence, and ambiguous send failures.

Docker is verified by the repository's `checks` workflow, not this local sandbox.
No live Discord integration test or real-model quality evaluation was performed.
