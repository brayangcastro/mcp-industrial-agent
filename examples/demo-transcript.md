# Demo transcript — Claude Desktop + industrial-mcp

Captured during a hot-day-in-June scenario. The model has access to
the six tools listed below and nothing else; the user types in plain
language and Claude orchestrates the tools without further prompting.

**Tools available:** `list_plants`, `list_silos`, `get_silo_thermometry`,
`list_motors`, `get_active_alerts`, `trigger_motor_action`.

---

**User:** ¿Qué tan caliente está la planta hoy?

**Claude:** *calls `list_plants()` → 1 plant. calls `get_active_alerts(plant_id="plant-nw-1", min_severity="warning")` → 2 alerts.*

> Hay dos silos con temperatura elevada en la planta del noroeste:
> - **silo-2** — temperatura máxima 31.4°C (warning)
> - **silo-7** — temperatura máxima 32.1°C (critical)
>
> ¿Quieres que revise el estado de los ventiladores asociados o que te muestre el detalle por capas?

---

**User:** Sí, dime qué ventilador puede ayudar al silo-7.

**Claude:** *calls `list_motors(plant_id="plant-nw-1", kind="fan")` → 28 fans. filters by silo_id="silo-7".*

> Silo-7 tiene 4 ventiladores: `fan-7-1`, `fan-7-2`, `fan-7-3`, `fan-7-4`. Los cuatro están detenidos (`stopped`).
>
> Con el ambiente actual a 28°C y el silo a 32.1°C, encender uno o dos podría empezar a enfriar la masa por la parte inferior. ¿Quieres que verifique si es seguro encender `fan-7-1`?

---

**User:** Sí, evalúalo en modo dry run.

**Claude:** *calls `trigger_motor_action(motor_id="fan-7-1", action="start", dry_run=True)`.*

```json
{
  "phase": "dry_run",
  "motor_id": "fan-7-1",
  "current_state": "stopped",
  "requested_action": "start",
  "would_execute": true,
  "preconditions": [
    { "name": "motor_state_known",  "passed": true,  "detail": "motor state = 'stopped'" },
    { "name": "no_active_fault",    "passed": true,  "detail": "motor not in fault state" },
    { "name": "action_meaningful",  "passed": true,  "detail": "start requested while motor is stopped" }
  ],
  "warnings": [],
  "next_step": "set dry_run=False, provide operator_id and reason"
}
```

> El simulacro pasa las tres precondiciones de seguridad y no hay
> advertencias activas. Para encenderlo de verdad necesitas un
> `operator_id` y una razón corta — y el servidor tiene que estar
> arrancado con `INDUSTRIAL_MCP_ALLOW_WRITES=true`. ¿Lo procedo o
> esperas a confirmación del operador?

---

**User:** Espera, mejor primero apaga `fan-2-1` que está corriendo de más.

**Claude:** *calls `trigger_motor_action(motor_id="fan-2-1", action="stop", dry_run=True)`.*

```json
{
  "phase": "dry_run",
  "motor_id": "fan-2-1",
  "current_state": "running",
  "requested_action": "stop",
  "would_execute": true,
  "preconditions": [
    { "name": "motor_state_known",  "passed": true, "detail": "motor state = 'running'" },
    { "name": "no_active_fault",    "passed": true, "detail": "motor not in fault state" },
    { "name": "action_meaningful",  "passed": true, "detail": "stop requested while motor is running" }
  ],
  "warnings": [
    "silo silo-2 is at 31.4°C — stopping fan may allow temperature rise; recommend operator review"
  ]
}
```

> Técnicamente sí se puede detener, pero hay una advertencia: silo-2
> está a 31.4°C — si lo apagas ahora la temperatura puede subir.
> ¿Aún así proceder, o prefieres dejarlo correr otro rato?

---

The point of this transcript is not that the model is clever — it's
that **every step is auditable, every action is gated, and the user
sees exactly which tools were called and why.** That is the model
deploying into a factory floor without anyone losing sleep.
