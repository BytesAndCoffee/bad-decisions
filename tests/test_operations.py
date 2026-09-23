from __future__ import annotations

from unittest.mock import Mock

import pytest

from bad_decisions import operations


@pytest.mark.parametrize("action", ("reload", "stop"))
def test_mutating_service_actions_require_root(action, monkeypatch, capsys):
    run = Mock()
    monkeypatch.setattr(operations.platform, "system", lambda: "Linux")
    monkeypatch.setattr(operations.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(operations.subprocess, "run", run)

    assert operations.run([action]) == 2
    assert capsys.readouterr().err == f"bad-decisions {action} must be run with sudo.\n"
    run.assert_not_called()


def test_status_remains_unprivileged(monkeypatch):
    completed = Mock(returncode=0)
    run = Mock(return_value=completed)
    monkeypatch.setattr(operations.platform, "system", lambda: "Linux")
    monkeypatch.setattr(operations.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(operations.subprocess, "run", run)

    assert operations.run(["status"]) == 0
    run.assert_called_once_with(["systemctl", "status", "bad-decisions.service"], check=False)


@pytest.mark.parametrize("action", ("reload", "stop"))
def test_root_can_run_mutating_service_actions(action, monkeypatch):
    completed = Mock(returncode=0)
    run = Mock(return_value=completed)
    monkeypatch.setattr(operations.platform, "system", lambda: "Linux")
    monkeypatch.setattr(operations.os, "geteuid", lambda: 0)
    monkeypatch.setattr(operations.subprocess, "run", run)

    assert operations.run([action]) == 0
    run.assert_called_once_with(["systemctl", action, "bad-decisions.service"], check=False)
