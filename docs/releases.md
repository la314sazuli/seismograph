# Versioned releases

Release preparation checks the source tree, the wheel, the source distribution,
and the Docker image. A release remains experimental until it has passed an
authorized, limited live pilot; passing synthetic checks does not certify
production capacity or real-model accuracy.

## Install a downloaded package

From a published GitHub release, download the wheel and `SHA256SUMS` together.
Use Python 3.12+ on Linux or macOS. These commands assume v0.4.0 artifacts are
already downloaded into the current directory; they do not install from PyPI.

```sh
sha256sum --ignore-missing --check SHA256SUMS
python -m venv .venv
. .venv/bin/activate
python -m pip install ./seismograph-0.4.0-py3-none-any.whl
python -m seismograph --version
python -m seismograph exposure-demo
```

On macOS, `shasum -a 256` can calculate a file's digest for comparison with
`SHA256SUMS`. These checksums detect mismatched bytes; they are not signed
provenance or independent proof of publisher identity. Dependencies may need
network access during installation. The default demos and evaluator do not
contact Discord or a model after installation.

The `.tar.gz` source distribution includes the runtime, fixtures, tests,
evaluation review tools, frozen challenge files, documentation, example
configuration, and Dockerfile. It can also be installed with pip, or unpacked
for development and Docker builds. GitHub's source archives are a separate way
to retrieve the complete tagged repository after a release is published.
No prebuilt container image or PyPI package is implied by a GitHub release.

## Upgrade safely

1. Stop the existing bot. Do not run two versions against the same SQLite file.
2. Take a consistent backup, including any required WAL state, using the
   [operations guide](operations.md). Keep it outside the working data directory.
3. Install the selected wheel or rebuild the image from the exact release source.
4. Confirm `python -m seismograph --version`, review `.env.example`, and preserve
   the existing hashing salt and approved collection settings.
5. Start only in an authorized environment. Schema v5 migrates older databases
   on startup; rollback to an older binary requires the pre-upgrade backup.

Version 0.4.0 keeps Perplexity Sonar as the primary documented setup and retains
compatible-provider support. It adds no runtime dependency or required secret.
For the new retained audit, exposure resets, and privacy behavior, read
[patch exposure](patch-exposure.md).

## Maintainer release gate

- **Version alignment:** Update `pyproject.toml`, `seismograph.__version__`, and
  the changelog together. Tests check alignment; the CLI exposes the version
  without configuration or network access.
- **Exact commit:** Open a focused release-preparation PR. Review its diff,
  then require all Python, Docker, and package jobs to pass on the exact head.
  Merge only that tested head and verify main's checks afterward.
- **Clean builds:** `python -m build` makes the source distribution first and
  builds a wheel from it. CI installs both artifacts into separate environments,
  checks installed metadata, and runs all five demos and the reference evaluator
  outside the checkout. The CI artifact contains the checked packages.
- **Draft first:** Create the draft against the full tested merge SHA, attach
  the checked distributions and SHA-256 checksum file, and record the commit,
  schema version, CI run, upgrade path, and limitations in the release notes.
- **Publication is separate:** Verify that the draft target has not moved and
  that the downloaded assets match their checksums before publishing. A draft
  is visible only to authorized repository collaborators and is not a public
  release. Do not treat creating a draft as deploying the bot or publishing to
  a package or container registry.

The offline reference evaluator replays supplied answers to test scorer
consistency. Its passing result is not a model-quality benchmark.
