from __future__ import annotations

import json
from datetime import datetime

import pytest

import automation
from tests.test_quality import valid_forecast


@pytest.fixture(autouse=True)
def freeze_automation_clock(monkeypatch):
    """Keep date-sensitive publication fixtures deterministic."""
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 7, 26, 18, 35)
            return value.replace(tzinfo=tz) if tz else value

    monkeypatch.setattr(automation, "datetime", FrozenDatetime)


def test_successful_update_writes_forecast_and_state(
    tmp_path, monkeypatch
) -> None:
    forecast = valid_forecast()
    monkeypatch.setattr(automation, "build_forecast", lambda: forecast)
    output = tmp_path / "public" / "data" / "forecast.json"
    state_dir = tmp_path / "state"

    written, quality = automation.run_update(
        output=output,
        state_dir=state_dir,
        performance_path=tmp_path / "performance.json",
        max_data_age_days=5,
    )

    assert quality.passed
    assert output.exists()
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["meta"]["automation"]["status"] == "passed"
    assert written["meta"]["automation"]["quality_gate"]["passed"]
    assert json.loads(
        (state_dir / "last_run.json").read_text(encoding="utf-8")
    )["status"] == "succeeded"


def test_failed_gate_preserves_previous_output(tmp_path, monkeypatch) -> None:
    previous = valid_forecast()
    output = tmp_path / "forecast.json"
    output.write_text(json.dumps(previous), encoding="utf-8")
    invalid = valid_forecast()
    invalid["sector_forecast"]["sectors"] = []
    monkeypatch.setattr(automation, "build_forecast", lambda: invalid)
    state_dir = tmp_path / "state"

    with pytest.raises(RuntimeError, match="质量门禁未通过"):
        automation.run_update(
            output=output,
            state_dir=state_dir,
            performance_path=tmp_path / "performance.json",
        )

    assert json.loads(output.read_text(encoding="utf-8")) == previous
    state = json.loads(
        (state_dir / "last_run.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "failed"
    assert state["output_preserved"] is True


def test_build_uses_node_directly(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(automation, "build_forecast", valid_forecast)
    monkeypatch.setattr(automation, "_find_node", lambda: "node.exe")
    vinext_cli = tmp_path / "node_modules" / "vinext" / "dist" / "cli.js"
    vinext_cli.parent.mkdir(parents=True)
    vinext_cli.touch()
    encrypt_script = tmp_path / "scripts" / "encrypt-forecast.mjs"
    prepare_script = tmp_path / "scripts" / "prepare-dist.mjs"
    prepare_script.parent.mkdir()
    encrypt_script.touch()
    prepare_script.touch()
    monkeypatch.setattr(automation, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(
        automation.subprocess,
        "run",
        lambda args, **kwargs: calls.append((args, kwargs)),
    )

    automation.run_update(
        output=tmp_path / "forecast.json",
        state_dir=tmp_path / "state",
        performance_path=tmp_path / "performance.json",
        run_build=True,
    )

    assert calls[0][0][0] == "node.exe"
    assert calls[0][0][-1].endswith("encrypt-forecast.mjs")
    assert calls[0][1]["check"] is True
    assert calls[1][0][0] == "node.exe"
    assert calls[1][0][-1] == "build"
    assert calls[2][0][0] == "node.exe"
    assert calls[2][0][-1].endswith("prepare-dist.mjs")


def test_failed_build_restores_previous_output(tmp_path, monkeypatch) -> None:
    previous = valid_forecast()
    previous["meta"]["release"] = "6"
    output = tmp_path / "forecast.json"
    output.write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr(automation, "build_forecast", valid_forecast)
    monkeypatch.setattr(
        automation,
        "_run_site_build",
        lambda: (_ for _ in ()).throw(RuntimeError("build failed")),
    )

    with pytest.raises(RuntimeError, match="build failed"):
        automation.run_update(
            output=output,
            state_dir=tmp_path / "state",
            performance_path=tmp_path / "performance.json",
            run_build=True,
        )

    restored = json.loads(output.read_text(encoding="utf-8"))
    assert restored["meta"]["release"] == "6"


def test_current_session_requirement_defers_without_replacing_output(
    tmp_path, monkeypatch
) -> None:
    previous = valid_forecast()
    output = tmp_path / "forecast.json"
    output.write_text(json.dumps(previous), encoding="utf-8")
    stale = valid_forecast()
    monkeypatch.setattr(automation, "build_forecast", lambda: stale)
    state_dir = tmp_path / "state"

    with pytest.raises(automation.UpdateDeferred, match="current-session data pending"):
        automation.run_update(
            output=output,
            state_dir=state_dir,
            performance_path=tmp_path / "performance.json",
            require_current_session=True,
        )

    assert json.loads(output.read_text(encoding="utf-8")) == previous
    state = json.loads((state_dir / "last_run.json").read_text(encoding="utf-8"))
    assert state["status"] == "deferred"
    assert state["output_preserved"] is True
