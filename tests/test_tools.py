"""Sanity tests for the read tools and the safety gating on writes.

These run in CI on a fresh checkout — no MQTT broker, no AWS, just the
in-memory mock adapter.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from industrial_mcp.adapters.mock import MockAdapter
from industrial_mcp.audit import AuditLog
from industrial_mcp.config import Config
from industrial_mcp.tools import Tools


@pytest.fixture
def tools() -> Tools:
    fd, path = tempfile.mkstemp(prefix="audit_", suffix=".jsonl")
    os.close(fd)
    config = Config(adapter="mock", audit_log_path=path, allow_writes=False)
    return Tools(adapter=MockAdapter(), config=config, audit=AuditLog(path))


def test_list_plants(tools: Tools) -> None:
    plants = tools.list_plants()
    assert len(plants) == 1
    assert plants[0]["id"] == "plant-nw-1"


def test_list_silos(tools: Tools) -> None:
    silos = tools.list_silos("plant-nw-1")
    ids = [s["id"] for s in silos]
    assert ids == [f"silo-{i}" for i in range(1, 8)]


def test_get_silo_thermometry_returns_minmax(tools: Tools) -> None:
    snap = tools.get_silo_thermometry("silo-1")
    assert snap["min_temp_c"] <= snap["avg_temp_c"] <= snap["max_temp_c"]


def test_list_motors_filter_by_kind(tools: Tools) -> None:
    fans = tools.list_motors("plant-nw-1", kind="fan")
    assert all(m["kind"] == "fan" for m in fans)
    assert len(fans) == 28  # 7 silos × 4 fans


def test_trigger_motor_action_defaults_to_dry_run(tools: Tools) -> None:
    result = tools.trigger_motor_action("fan-1-1", "start")
    assert result["phase"] == "dry_run"
    assert "would_execute" in result


def test_trigger_motor_action_blocks_live_without_writes_enabled(tools: Tools) -> None:
    result = tools.trigger_motor_action(
        "fan-1-1", "start", dry_run=False, operator_id="op-42", reason="test"
    )
    assert result["ok"] is False
    assert result["phase"] == "rejected_server_policy"


def test_trigger_motor_action_blocks_live_without_operator_id(tools: Tools) -> None:
    result = tools.trigger_motor_action("fan-1-1", "start", dry_run=False)
    assert result["ok"] is False
    assert result["phase"] == "rejected"


def test_trigger_motor_action_executes_when_fully_authorized() -> None:
    fd, path = tempfile.mkstemp(prefix="audit_", suffix=".jsonl")
    os.close(fd)
    config = Config(adapter="mock", audit_log_path=path, allow_writes=True)
    tools = Tools(adapter=MockAdapter(), config=config, audit=AuditLog(path))

    result = tools.trigger_motor_action(
        "fan-1-1",
        "start",
        dry_run=False,
        operator_id="op-42",
        reason="warm silo, manual start",
    )
    assert result["phase"] == "executed"
    assert result["ok"] is True
    assert result["new_state"] == "running"


def test_unknown_motor_returns_clean_error(tools: Tools) -> None:
    result = tools.trigger_motor_action("does-not-exist", "start")
    assert result["ok"] is False
    assert result["phase"] == "lookup"
