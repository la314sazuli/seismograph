# Contributing

Thanks for taking the time. Small, focused changes are much easier to review than
large ones.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
python -m seismograph demo
python -m seismograph changes-demo
python -m seismograph review-demo
python -m seismograph exposure-demo
```

The demos and the test suite run without a Discord token or an API key.
Keep feature development reproducible offline; provider or server access is
not a prerequisite for contributing code and regression tests. Never present
recorded outputs as real-model results.

## Making a change

1. Branch from `main`, for example `git checkout -b fix/jump-link-guild-id`.
2. Make the change and add or adjust a test that would fail without it.
3. Run `pytest`, `ruff check .`, and `ruff format .`.
4. Open a pull request and fill in the template.

## What reviewers look for

- One concern per pull request. Split unrelated changes.
- No new dependency unless the change cannot reasonably be made without it.
  Explain the need in the pull request.
- No new abstraction layer, framework, or configuration option that the current
  behaviour does not use.
- Ranking stays deterministic and in application code. The model classifies and
  selects evidence; it does not decide scores.
- Every reported signal still traces back to message ids that were present in the
  analyzed input.
- Changes to stored data, redaction, or hashing are called out explicitly.

## Never include real data

Do not put real Discord messages, usernames, user ids, guild ids, tokens, API
keys, or database files in issues, pull requests, tests, or fixtures. Use
fictional examples in the style of `seismograph/fixtures/synthetic_messages.json`.
