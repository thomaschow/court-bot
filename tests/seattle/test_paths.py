from pathlib import Path

import pytest

from seattle_courtbot import paths


def test_paths_honour_courtbot_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COURTBOT_ROOT", str(tmp_path))
    assert paths.project_root() == tmp_path
    assert paths.session_path() == tmp_path / "state" / "session" / "seattle.json"
    assert paths.ledger_path() == tmp_path / "state" / "seattle.sqlite"
    assert paths.log_path() == tmp_path / "state" / "logs" / "seattle-courtbot.jsonl"


def test_config_path_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "custom.yaml"
    monkeypatch.setenv("SEATTLE_COURTBOT_CONFIG", str(custom))
    assert paths.config_path() == custom
