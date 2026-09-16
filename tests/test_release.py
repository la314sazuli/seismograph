import tomllib
from pathlib import Path
from unittest.mock import Mock

import pytest

from seismograph import __version__
from seismograph.__main__ import main

ROOT = Path(__file__).resolve().parents[1]


def test_source_version_matches_package_metadata():
    with (ROOT / "pyproject.toml").open("rb") as file:
        assert tomllib.load(file)["project"]["version"] == __version__


def test_version_command_needs_no_configuration_or_provider(monkeypatch, capsys):
    monkeypatch.setattr(
        "seismograph.__main__.load_config", Mock(side_effect=AssertionError("No configuration"))
    )
    monkeypatch.setattr(
        "seismograph.analysis.LLMClient.complete_json", Mock(side_effect=AssertionError("No model"))
    )
    with pytest.raises(SystemExit) as result:
        main(["--version"])
    assert result.value.code == 0
    assert capsys.readouterr().out == f"seismograph {__version__}\n"


def test_changelog_has_the_current_version():
    assert f"## {__version__}:" in (ROOT / "CHANGELOG.md").read_text()
