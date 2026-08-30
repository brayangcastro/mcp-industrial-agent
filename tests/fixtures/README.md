# ESP32 fixtures

Responses from a real SiloScan module (`agrostar-s3-onewire` 0.3.0, device
`banco-silo3`) captured on 2026-08-28 over the LAN.

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
| `esp32_probe_i0_populated.derived.json` | **derived** | Multi-sensor shape with one `fault` channel, from `web_server.h:34-46` + `payload.h:31-35`. Kept because no real probe has been faulted yet; replace once one is. |

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
