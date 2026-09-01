# Demo transcript — Claude Desktop + industrial-mcp

**A real session, captured 2026-08-30.** Claude Desktop over stdio, the
`esp32` adapter, and a SiloScan module on the bench: an ESP32-S3 with two
DS18B20 probes on a OneWire mux and a relay on GPIO5 with the onboard RGB
mirroring it — green on, red off.

Every number below came off that hardware. Nothing here is a scenario.

> An earlier version of this file was an invented 7-silo plant with 28 fans.
> It was replaced rather than kept alongside: a repo arguing that models
> should not report numbers nobody measured had no business shipping a
> transcript of a plant that does not exist.

**Tools available:** `list_plants`, `list_silos`, `get_silo_thermometry`,
`list_motors`, `get_active_alerts`, `trigger_motor_action`.

---

## Reading

**User:** ¿Cómo está la temperatura de los silos?

**Claude:** *calls `list_plants()` → `banco-silo3`. calls `list_silos()` →
`silo-3`. calls `get_silo_thermometry("silo-3")` → two live OneWire reads.*

> Silo-3 (módulo banco-silo3) — lectura de hace un momento:
>
> - Promedio: 27.25 °C
> - Mín: 26.5 °C / Máx: 28 °C
> - 2 cables, 2 sensores, todos válidos, ninguno en falla
> - Sin alertas activas en la planta
>
> Todo estable: solo 1.5 °C de diferencia entre el punto más frío y el más
> caliente. No reporta porcentaje de llenado ni capacidad (vienen en `null`),
> así que si necesitas ese dato habría que revisarlo por otro lado.

Verified against the device in the same minute, outside the MCP path:

```
GET /api/probe?i=0 → {"estado":"valid","temp":26.5,"rom":"2839a47997140315"}
GET /api/probe?i=8 → {"estado":"valid","temp":28,  "rom":"28142007d6013cf6"}
```

The min and max the model spoke are the two physical probes, one for one.

Note what it did with `capacity_t` and `fill_pct`. The module measures grain
temperature, not level, so the adapter returns `null` — and the model passed
that on as "you'd have to check elsewhere" instead of reporting an empty silo.
`0` there would have been a plausible, wrong answer.

---

## Writing — the gate refusing first

**User:** apaga ventilador

**Claude:** *calls `list_motors()` → one fan. calls
`trigger_motor_action(motor_id="fan-1", action="stop")` — dry run by default.*

> Encontré un solo ventilador en el módulo banco-silo3:
>
> **fan-1** — silo-3, estado: **en marcha** (pin 5)
>
> Hice la simulación del paro y pasa todas las precondiciones, pero salió una
> advertencia: **silo-3 está a 28.0 °C**, y detener el ventilador puede dejar
> que la temperatura suba.
>
> Para ejecutar el paro de verdad necesito dos cosas que quedan en el registro
> de auditoría: tu ID de operador, y un motivo breve. Confírmame que sigo
> adelante con fan-1 y me pasas el ID, y lo detengo.

**The relay did not move.** Nobody told the model to be careful — it was asked
to turn the fan off and it came back asking for credentials, because the tool
contract left no other path. The `state: "en marcha"` it reported was read off
the pin, not from the last command sent.

---

## Writing — execution

**User:** Apaga el ventilador fan-1 de verdad. Usa `dry_run=False`,
`operator_id` "brayan" y como razón "prueba del relé".

**Claude:** *calls `trigger_motor_action(motor_id="fan-1", action="stop",
dry_run=False, operator_id="brayan", reason="prueba del relé")`.*

> Listo. fan-1 detenido — comando enviado y registrado en el log de auditoría.
> Estado actual: `off`, comandado `off`.
>
> Sigue en pie la advertencia del silo-3 a 28.0 °C: sin ventilación la
> temperatura puede subir.

The LED went green → red inside the HTTP round trip. Two more cycles followed
(`prendelo`, then `apágalo`), and after the third the model volunteered
something nobody asked for:

> Llevamos tres ciclos de arranque/paro en pocos minutos, lo cual desgasta más
> el relé y el motor que la operación normal. Si estás probando el relé, quizá
> convenga espaciar los ciclos.

---

## What the audit log kept

```
20:22:40  motor.stop   fan-1  applied  actor=brayan  reason: prueba del relé
          WARNING: silo silo-3 is at 28.0°C — stopping fan may allow
                   temperature rise; recommend operator review
20:23:03  motor.start  fan-1  applied  actor=brayan  reason: fin de prueba del relé
20:23:35  motor.stop   fan-1  applied  actor=brayan  reason: paro manual solicitado
          WARNING: silo silo-3 is at 28.0°C — …
```

Three executed actions. The dry run is not there, because dry runs are not
interesting and would dilute the signal.

**The warning is in the record, not just on the screen.** Someone reading this
file after the fact can see that the operator was told the grain was warm and
stopped the fan anyway. That is why advisory warnings are structured data
rather than prose in a reply.

---

## The honest limits of this demo

- **`fan-1` is a relay driving an LED**, not a three-phase fan. No inertia, no
  inrush, no interlocks, no run-feedback contact.
- **The server was started with `INDUSTRIAL_MCP_ALLOW_WRITES=true`** for this
  session. That is the one layer a prompt cannot reach, and it was deliberately
  opened. Read-only is the default for a reason.
- **The `mismatch` path has since fired on hardware** (2026-08-31, firmware
  0.6.0 with a feedback input on a second GPIO). With the feedback wire pulled
  and every write gate open, the call came back `blocked_by_safety` — refused on
  physical evidence rather than a missing permission. See
  [`docs/hardware-verification.md`](../docs/hardware-verification.md) §4c.

Full evidence, including what remains unverified:
[`docs/hardware-verification.md`](../docs/hardware-verification.md)
([español](../docs/verificacion-hardware.md)).
