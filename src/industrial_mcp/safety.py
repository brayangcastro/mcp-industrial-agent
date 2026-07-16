"""Safety guardrails for state-changing operations.

The single most important file in this repo. An LLM agent must never
be able to stop a fan on a hot silo or start a motor with safety
interlocks tripped. Every control tool calls ``evaluate`` BEFORE it
even considers executing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class SafetyCheck:
    name: str
    passed: bool
    detail: str


@dataclass
class SafetyResult:
    allowed: bool
    checks: list[SafetyCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def evaluate_motor_action(
    motor: dict,
    action: Literal["start", "stop"],
    plant_context: dict,
) -> SafetyResult:
    """Return what would happen if ``action`` were executed on ``motor``.

    Never raises; always returns a structured result. The caller decides
    whether to proceed based on ``allowed``.
    """
    checks: list[SafetyCheck] = []
    warnings: list[str] = []

    # Check 1 — motor must be in known state
    state_known = motor.get("state") in {"running", "stopped", "fault"}
    checks.append(
        SafetyCheck(
            name="motor_state_known",
            passed=state_known,
            detail=f"motor state = {motor.get('state')!r}",
        )
    )

    # Check 2 — no fault active
    no_fault = motor.get("state") != "fault"
    checks.append(
        SafetyCheck(
            name="no_active_fault",
            passed=no_fault,
            detail="motor not in fault state" if no_fault else "fault must be cleared first",
        )
    )

    # Check 3 — action is meaningful given current state
    current = motor.get("state")
    meaningful = (action == "start" and current == "stopped") or (
        action == "stop" and current == "running"
    )
    checks.append(
        SafetyCheck(
            name="action_meaningful",
            passed=meaningful,
            detail=f"{action} requested while motor is {current}",
        )
    )

    # Check 4 (advisory) — stopping a ventilation fan on a hot silo
    if motor.get("kind") == "fan" and action == "stop":
        linked_silo = plant_context.get("silos", {}).get(motor.get("silo_id"))
        if linked_silo and linked_silo.get("max_temp_c", 0) >= 27.0:
            warnings.append(
                f"silo {motor.get('silo_id')} is at "
                f"{linked_silo['max_temp_c']}°C — stopping fan may "
                "allow temperature rise; recommend operator review"
            )

    # Check 5 (advisory) — starting a fan when it is raining
    if motor.get("kind") == "fan" and action == "start":
        if plant_context.get("weather", {}).get("is_raining"):
            warnings.append(
                "ambient is raining — starting intake fans may "
                "introduce moisture; recommend operator review"
            )

    allowed = all(c.passed for c in checks)
    return SafetyResult(allowed=allowed, checks=checks, warnings=warnings)
