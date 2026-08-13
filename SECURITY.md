# Security

## Reporting a vulnerability

Please report vulnerabilities privately. Do not open a public issue.

Use GitHub's private vulnerability reporting on this repository: open the
**Security** tab and choose **Report a vulnerability**. That channel is preferred
because it keeps the discussion private until a fix is available. If it is
unavailable to you, contact the repository owner through their GitHub profile and
ask for a private channel before sharing details.

Please include what you observed, how to reproduce it, and the impact you expect.
Do not include real user data, real Discord message content, or live credentials
in a report. Redact anything sensitive and describe it instead.

Expect an acknowledgement within a few days. Please allow a reasonable period for
a fix before public disclosure.

## Scope

In scope: anything in this repository, in particular the handling of
`AUTHOR_HASH_SALT`, `DISCORD_TOKEN`, and `LLM_API_KEY`; the redaction filter in
`seismograph/privacy.py`; the validation of model output in
`seismograph/analysis.py`; and prompt-injection resistance in the analysis step.

Out of scope: vulnerabilities in Discord, in your LLM provider, and in
deployments that expose a `.env` file or a database file. This project is
distributed under the MIT License with no warranty.

## Operator notes

- Give the bot the minimum permissions listed in the README, and only in the
  channels you intend to analyze.
- Keep `AUTHOR_HASH_SALT` secret. Anyone holding it can test whether a given
  Discord id appears in the database.
- Mount the SQLite database on storage you control and include it in whatever
  data-handling policy applies to your community.
