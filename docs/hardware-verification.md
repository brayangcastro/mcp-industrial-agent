# Hardware verification

> *Versión en español: [`verificacion-hardware.md`](verificacion-hardware.md).*

Until 2026-08-28 this repo shipped one adapter: a deterministic mock. The
README said so, and the honest version of the pitch was *"the shape is real,
the data is not."*

This document is the record of closing that gap. The adapter now reads a
physical device, and a language model answered a question about grain
temperature with a number that came off a sensor sitting on a bench.

**What follows is evidence, not a claim.** Where something was measured, the
measurement is here. Where something is still assumed, it is in
[Not verified](#not-verified) — a repo about not reporting numbers you did not
measure has no business overstating its own test coverage.

---

## The equipment

Not the equipment this was planned against. The plan was written for
`agrostar-termometria` — an ESP32 with seven MAX31856 thermocouple amplifiers
on SPI. What was actually on the bench was a different module from the same
family, and the adapter was written against the one that exists:

| | |
|---|---|
| Firmware | `agrostar-s3-onewire`, device `banco-silo3`, silo 3. Read path verified on **0.3.0**; **0.4.0** added `/api/relay` for the write path |
| MCU | ESP32-S3 (CH343 USB serial) |
| Sensing | DS18B20 on OneWire behind a 16-channel mux, **external VDD** (not parasitic) |
| Probes | 2 × DS18B20 on mux channels 0 and 8, ROMs `2839a47997140315` and `28142007d6013cf6` |
| Link | HTTP over LAN; also serves a WPA2 AP at `192.168.4.1` |
| Power mode | `continua` — the radio stays up, so there is no deep sleep to work around |

The read path is `GET /api/probe?i=N`, which triggers a **live** OneWire
conversion. The alternative, `/api/cable?i=N`, replays the last stored sweep.
Live was the right call: it is what makes a demo possible where you warm a
sensor with your hand and watch the number move.

---

## What was verified

### 1. The adapter reads real sensors (2026-08-29)

Two probes, one held in a hand, the other dropped in a glass of cold water.
Sampled through `Esp32Adapter.get_silo_thermometry`:

```
[  0s] min 27.0  max 28.5  avg 27.8
[ 31s] min 27.0  max 28.0  avg 27.5     ← stable
[ 45s] min 28.0  max 29.5  avg 28.8     (+1.0)
[ 59s] min 16.5  max 31.0  avg 23.8     (+2.5)
```

**The average fell while one probe was heating.** 31.0 and 16.5 average to
23.8, which resembles neither. A consumer reading only `avg` would have seen a
plant cooling down while one point climbed four degrees and another froze.

That is the thesis of this repo, measured rather than argued — and it is why
`get_silo_thermometry` returns `min`, `max`, `sensors_valid` and
`faulted_sensors` alongside the average instead of just the average.

### 2. A real fault, captured rather than fabricated (2026-08-29)

Momentarily grounding the data line produced one bad read in 451 polls
(98 seconds):

```json
{"sensores":[{"idx":0,"punto":"P1","estado":"fault","temp":null,
              "rom":"2839a47997140315"}],"present":true}
```

The ROM matches the good read and `present` is still `true`: the sensor
enumerated and then failed the scratchpad CRC. **That state cannot be faked by
unplugging the probe** — a clean disconnect yields `present:false` with an
empty `sensores` array. Which is why every fixture in `tests/fixtures/` is now
recorded from hardware and none are derived. See
[`tests/fixtures/README.md`](../tests/fixtures/README.md).

### 3. A model answered from the sensor (2026-08-30)

Claude Desktop, configured with `INDUSTRIAL_MCP_ADAPTER=esp32` over stdio, was
asked *"¿cómo está la temperatura de los silos?"* and answered:

> Silo-3 (módulo banco-silo3) — Promedio: 27.25 °C · Mín: 26.5 °C / Máx: 28 °C
> · 2 cables, 2 sensores, todos válidos, ninguno en falla · Sin alertas activas

Cross-checked against the device in the same minute, **outside** the MCP path:

```
GET /api/probe?i=0 → {"estado":"valid","temp":26.5,"rom":"2839a47997140315"}
GET /api/probe?i=8 → {"estado":"valid","temp":28,  "rom":"28142007d6013cf6"}
```

The min and max the model spoke are the two physical probes, one for one. Host
log:

```
14:20:58 [LocalMcpServerManager] Connecting to industrial
14:21:07 [LocalMcpServerManager] Connected to industrial (6 tools)
14:21:07 [localMcpBridge] announcing industrial: 6 tool(s)
```

### 4. The safety gate held a physical pin (2026-08-30)

Firmware 0.4.0 added `/api/relay`: `POST` drives GPIO5, `GET` reports the level
**read back off the pad**, alongside the command it was given and a `mismatch`
flag. The onboard RGB mirrors it for the camera — green on, red off — and is
deliberately outside the check, because a WS2812B is write-only and cannot be
read.

The three layers, run in order against that pin:

| Step | Result | Pin afterwards |
|---|---|---|
| 1. `trigger_motor_action` with defaults | `phase: dry_run` | `off` |
| 2. `dry_run=False`, no `operator_id` | `phase: rejected` | `off` |
| 3. operator + reason, server read-only | `phase: rejected_server_policy` | `off` |
| 4. all four conditions met | `phase: executed` | **`on`** |

and one line in the audit log, only for the call that happened:

```json
{"ts": 1788144400.76, "actor": "op-42", "action": "motor.start",
 "target": "fan-1", "outcome": "applied",
 "details": {"reason": "demo tres capas", "warnings": []}}
```

**The advisory rule fired against a measured temperature.** Asking to *stop*
the fan returned:

```
silo silo-3 is at 28.0°C — stopping fan may allow temperature rise;
recommend operator review
```

That rule has existed since the first commit. Until now the number in it came
out of a hash; 28.0 came off a DS18B20.

**What the adapter refuses to do:** `apply_motor_action` decides success by
reading the pin, not by the module accepting the POST. A 200 response with the
pin still low comes back `ok: False` with `state: "fault"` — telling an operator
a fan is running while the grain keeps heating is the physical version of
averaging a dead sensor as 0 °C.

### 5. The refusals hold when the module is blind

With no probe attached, the same adapter returns the opposite, and that is also
correct:

```json
{"cable_count": 0, "sensors_total": 0, "error": "no_valid_readings",
 "min_temp_c": null, "max_temp_c": null, "avg_temp_c": null}
```

plus an alert, because silence and a cool silo look identical to a consumer:

```json
[{"kind": "sensor_health", "severity": "warning",
  "detail": "0 sensor(s) seen on cable(s) [0], none classified valid by the module"}]
```

In the same session the model was told `capacity_t` and `fill_pct` are `null`
and passed that on to the user as *"you'd have to check that elsewhere"* — the
module measures grain temperature, not level, and `0` there would read as an
empty silo.

---

## Transport and exposure

This is MCP over **stdio**. The MCP host launches the server as a local
process and talks to it over stdin/stdout. Nothing reaches the module from
outside the LAN, and no vendor touches it.

That matters because **the module has no authentication**. `POST /api/config`
edits NVS with no credential at all. It is acceptable on a lab LAN for a demo
and it is not acceptable in a plant. If this were ever exposed remotely, an
authenticated gateway goes in front of it — not the module.

The adapter is read-only by construction: it calls three GET endpoints and
nothing else.

---

## What this cost the firmware's reputation, honestly

Two findings came out of the bench work that reflect on the devices, not on
this repo. Both are recorded because they are the kind of thing that gets
quietly dropped from a success story.

### Polling too fast takes the module off the network

Probing both cables **once per second** killed the HTTP server in about 15
seconds, and it did not come back on its own — 60 seconds of `curl` returned
timeouts while the board **still answered ping**. Only a reset recovered it.
That combination is what makes it deceptive: it looks like a network fault and
it is firmware.

`/api/probe` runs the OneWire read inside the AsyncTCP callback
(`web_server.h:80-91`). `probeCable` → `owScanCable` → `delay(dsConvMs())`, and
`dsConvMs()` is 125 ms at 9-bit resolution. Two cables per sample is a quarter
second of hard `delay()` per second inside the task that also has to service
TCP; the event queue backs up and never drains. lwIP answers ICMP from a
different task, which is why ping survives.

The firmware's own header comment states the rule it broke:

> *"El barrido sigue siendo diferido: `/api/scan` marca el flag y el lazo del
> modo config lo corre (**nunca dentro del handler async**)."*

`/api/scan` honors it — it sets a flag and returns 202. `/api/probe`, added
later for live reads, did not inherit the defense.

**Mitigation in this repo today:** do not poll faster than once every 5
seconds. Verified stable for 60 continuous seconds at that rate with 2/2
sensors valid. **The real fix belongs in the firmware** — defer the probe the
way the sweep is deferred.

The part worth keeping: throughout the outage the adapter reported
`device_unreachable` and never invented a temperature. But a consumer that
polls aggressively **can blind the equipment it is monitoring**, and in a real
silo that is self-inflicted blindness. Rate limiting belongs in the adapter,
not in the discipline of whoever calls it.

### The other firmware in the same family fails silently

`agrostar-s3-onewire` does the honest thing at the device level, with more care
than this repo asked for: `classifyChannel` separates `CH_EMPTY` / `CH_BAD_CRC`
/ `CH_OUT_OF_RANGE` / `CH_OK`, only `CH_OK` yields a temperature, and it
specifically catches exactly 85.00 °C — the DS18B20 scratchpad power-on-reset
value, which passes CRC and would otherwise read as a good measurement from a
sensor that never completed a conversion.

`agrostar-termometria`, the firmware this plan was originally written for, does
the opposite: it generates 133 mock values and overwrites only the ones the
MAX31856 read cleanly (`main.cpp:21-46`). **On failure the mock value stays** —
a plausible ~24 °C indistinguishable from a measurement. If that firmware is
ever used, it needs a per-sensor `real` flag in `readingToJson` before an
adapter can be trusted against it.

---

## Not verified

Things believed but not demonstrated. They are separated here on purpose.

**A relay is not a motor.** GPIO5 drives a logic-level pin with the onboard LED
mirroring it. Nothing with inertia, inrush current, or a thermal overload has
been switched. The gate was verified against *an actuator that moves*, which is
the part that was previously missing — but sequencing a real three-phase fan
brings interlocks, run-feedback contacts and stop-category requirements that
none of this addresses.

**The mismatch path has not been triggered on hardware.** `state != commanded`
is the branch that turns a stuck pin into a `fault`, and it is covered only by a
constructed payload in the test suite. Forcing it for real needs a jumper
holding GPIO5 down while the firmware commands it high. Until that is done, the
detection is *implemented and unit-tested, not demonstrated*.

**Only one actuator exists.** `RELAY_ID` is a compile-time constant, so the
module answers for exactly `fan-1`. Multiple relays would need the id to become
a lookup, and `POST` currently 404s on anything else — which is the right
failure, but it is not a fleet.

**A grounded bus may report 0.0 °C as valid.** Read from the code, not
measured. `owReadSensor` accepts a scratchpad when `crc8(sp,8) == sp[8]`. A
sustained short to ground samples every bit as 0, and Dallas CRC-8 over eight
zero bytes is zero — which is what sits in `sp[8]`. The CRC would pass,
`decodeScratchpadTemp` would return `0.0`, and `classifyChannel` would approve
it: not NaN, inside −50..125, not 85.00. It would emerge as
`estado: "valid", temp: 0`.

An attempt to confirm this on 2026-08-30 failed to reproduce it — 90 seconds of
polling gave 394 `valid` reads at 26.0–26.5 °C and no zeros, because the short
was not held for the window. **Unproven, not refuted.** A competing hypothesis
to rule out: that `g_ow.reset()` sees no presence pulse with the line held down
and aborts before reading.

If it is confirmed, the fix does not require guessing temperatures: a genuine
scratchpad is never all zeros. Byte 4 is the configuration register (`0x1F` at
9-bit), byte 5 is reserved `0xFF`, byte 7 is always `0x10`. Validating one of
those before accepting the CRC sends the fabricated `0.00` to `CH_BAD_CRC`,
which is where it belongs.

This matters more to the deployed fleet than to this repo: a thermometry cable
that gets grounded in a silo junction box would today report 0 °C wearing the
face of good data, and hot grain averages downward.

---

## Reproducing this

```bash
INDUSTRIAL_MCP_ADAPTER=esp32 \
INDUSTRIAL_MCP_ESP32_HOST=192.168.1.100 \
INDUSTRIAL_MCP_ESP32_CABLES=0,8 \
uv run python -c "
from industrial_mcp.adapters.esp32 import Esp32Adapter
print(Esp32Adapter.from_env().get_silo_thermometry('silo-3'))
"
```

`INDUSTRIAL_MCP_ESP32_CABLES` defaults to channel `0` alone. Leaving it out
silently drops every other probe — each channel costs a OneWire conversion, so
the default is cheap rather than complete.

For an MCP host, see [`examples/claude-desktop-config.json`](../examples/claude-desktop-config.json).

**The address moves.** This module took three different DHCP leases in three
reboots (`.71` → `.69` → `.100`). Anything that pins the IP in a file goes
stale on the next power cycle. Reserve it by MAC, or read the address off the
serial console at boot.
