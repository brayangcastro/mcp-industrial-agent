# ESP32 fixtures

Responses from a real SiloScan module (`agrostar-s3-onewire`, device
`banco-silo3`) captured over the LAN: the sensor captures on 2026-08-28 against
firmware 0.3.0, the relay captures on 2026-08-30 against 0.4.0.

| File | Source | Notes |
|---|---|---|
| `esp32_status.json` | **recorded** | `GET /api/status` |
| `esp32_config.json` | **recorded** | `GET /api/config` |
| `esp32_last.json` | **recorded** | `GET /api/last` — summary of the last full scan |
| `esp32_historial.json` | **recorded** | `GET /api/historial` — note `temp_max: null`, not `0` |
| `esp32_probe_i0.json` | **recorded** | `GET /api/probe?i=0` with **no cable attached** |
| `esp32_logs.txt` | **recorded** | `GET /api/logs` — plain text ring buffer, not JSON |
| `esp32_probe_i0_populated.json` | **recorded** | `GET /api/probe?i=0`, one DS18B20 warmed by hand — 31.5 °C |
| `esp32_probe_i8_populated.json` | **recorded** | `GET /api/probe?i=8`, one DS18B20 chilled in a glass — 15.5 °C |
| `esp32_last_populated.json` | **recorded** | `GET /api/last` with both cables present |
| `esp32_probe_fault.json` | **recorded** | `GET /api/probe?i=0` with the data line briefly grounded — ROM enumerates, scratchpad fails CRC, `estado: fault` / `temp: null` |
| `esp32_relay_off.json` | **recorded** | `GET /api/relay`, actuator off and feedback agreeing (0.6.0) |
| `esp32_relay_on.json` | **recorded** | `GET /api/relay`, actuator on and feedback agreeing (0.6.0) |
| `esp32_relay_mismatch.json` | **recorded** | `GET /api/relay` with the feedback wire removed — commanded `on`, `drive` on, feedback `off`. A real actuator that did not follow |

**Every fixture here is recorded from hardware. None are derived.** The fault
capture took 451 polls over 98 seconds of wiggling one connector; it is the
same physical probe as `esp32_probe_i0_populated.json` (identical `rom`), which
is what makes the pair useful — the same sensor, reading and then not reading.

Per sensor, `estado` can only be `valid` or `fault`. The vocabulary in
`payload.h:31-35` also defines `open` (`CH_EMPTY`), but `owScanCable` cannot
emit it at the sensor level: a sensor is only added to the array once its ROM
enumerates, and `owReadSensor` then overwrites `kind` with
`classifyChannel(true, true, …)`, which never returns `CH_EMPTY`. An absent
cable surfaces as `present: false` with an empty `sensores` array instead —
that is `esp32_probe_i0.json`.

The empty captures were taken with **nothing plugged into the mux**, which is
why every temperature is absent. That is not a degenerate case to work around —
it is the fixture behind one of the most important tests in the suite: a module
that cannot see a sensor must not report a temperature.

The two populated captures were taken simultaneously with one probe warm and
one cold, 16 degrees apart and both valid. Their average, 23.5 °C, resembles
neither — which is the case the snapshot's `min_temp_c` / `max_temp_c` exist to
keep visible.

`esp32_relay_mismatch.json` is a real actuator failure, captured 2026-08-31 with
the feedback wire off: commanded `on`, output pin driving, feedback still `off`.
It took firmware 0.6.0 to make it capturable at all.

⚠️ **An earlier version of this file said the capture needed "a jumper holding
GPIO5 down while the firmware drives it high." Do not do that** — shorting a
driven-HIGH ESP32-S3 output to ground draws far past the 40 mA per-pad maximum,
and any resistor big enough to be safe cannot pull the pin down anyway. It was
also measuring the wrong failure: reading back your own output only catches a
dead GPIO driver, not a relay that failed to close. 0.6.0 added a separate
feedback input, so the fault is now produced by **removing a wire**.

One case in the suite is still constructed and cannot be otherwise: a **dead
output stage** (`drive` off when commanded on). Producing that for real means
damaging a pin. It lives inline in `test_esp32_adapter.py` and says so where it
is used.
