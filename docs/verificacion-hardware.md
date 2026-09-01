# Verificación de hardware

> *English version: [`hardware-verification.md`](hardware-verification.md).*

Hasta el 2026-08-28 este repo traía un solo adapter: un mock determinista. El
README lo decía, y la versión honesta del pitch era *"la forma es real, los
datos no."*

Este documento es el registro de cerrar esa brecha. El adapter ahora lee un
dispositivo físico, y un modelo de lenguaje respondió una pregunta sobre
temperatura de grano con un número que salió de un sensor puesto en una mesa
de trabajo.

**Lo que sigue es evidencia, no una afirmación.** Donde algo se midió, la
medición está aquí. Donde algo sigue siendo un supuesto, está en
[Sin verificar](#sin-verificar) — un repo que trata de no reportar números que
no se midieron no tiene derecho a exagerar su propia cobertura de pruebas.

---

## El equipo

No es el equipo contra el que se planeó esto. El plan se escribió para
`agrostar-termometria` — un ESP32 con siete amplificadores de termopar
MAX31856 por SPI. Lo que realmente había en la mesa era otro módulo de la
misma familia, y el adapter se escribió contra el que existe:

| | |
|---|---|
| Firmware | `agrostar-s3-onewire`, dispositivo `banco-silo3`, silo 3. Camino de lectura verificado en **0.3.0**; **0.4.0** agregó `/api/relay`; **0.5.0** agregó mDNS; **0.6.0** separó el actuador en pin de accionamiento y pin de realimentación |
| MCU | ESP32-S3 (serial USB CH343) |
| Sensado | DS18B20 por OneWire detrás de un mux de 16 canales, **VDD externo** (no parásito) |
| Sondas | 2 × DS18B20 en canales 0 y 8 del mux, ROMs `2839a47997140315` y `28142007d6013cf6` |
| Enlace | HTTP por LAN; también sirve un AP WPA2 en `192.168.4.1` |
| Modo de energía | `continua` — el radio se queda encendido, así que no hay deep sleep que sortear |

La ruta de lectura es `GET /api/probe?i=N`, que dispara una conversión OneWire
**en vivo**. La alternativa, `/api/cable?i=N`, repite el último barrido
almacenado. En vivo era la decisión correcta: es lo que hace posible una demo
donde calientas un sensor con la mano y ves moverse el número.

---

## Qué se verificó

### 1. El adapter lee sensores reales (2026-08-29)

Dos sondas, una sostenida en la mano, la otra metida en un vaso de agua fría.
Muestreado a través de `Esp32Adapter.get_silo_thermometry`:

```
[  0s] min 27.0  max 28.5  avg 27.8
[ 31s] min 27.0  max 28.0  avg 27.5     ← stable
[ 45s] min 28.0  max 29.5  avg 28.8     (+1.0)
[ 59s] min 16.5  max 31.0  avg 23.8     (+2.5)
```

**El promedio bajó mientras una sonda se estaba calentando.** 31.0 y 16.5
promedian 23.8, que no se parece a ninguno de los dos. Un consumidor que solo
leyera `avg` habría visto una planta enfriándose mientras un punto subía
cuatro grados y otro se congelaba.

Esa es la tesis de este repo, medida en lugar de argumentada — y es la razón
por la que `get_silo_thermometry` regresa `min`, `max`, `sensors_valid` y
`faulted_sensors` junto con el promedio, en lugar de solo el promedio.

### 2. Una falla real, capturada en lugar de fabricada (2026-08-29)

Aterrizar momentáneamente la línea de datos produjo una lectura mala en 451
polls (98 segundos):

```json
{"sensores":[{"idx":0,"punto":"P1","estado":"fault","temp":null,
              "rom":"2839a47997140315"}],"present":true}
```

El ROM coincide con la lectura buena y `present` sigue en `true`: el sensor
enumeró y luego falló el CRC del scratchpad. **Ese estado no se puede
falsificar desconectando la sonda** — una desconexión limpia da
`present:false` con un arreglo `sensores` vacío. Por eso cada fixture en
`tests/fixtures/` está ahora grabado desde hardware y ninguno es derivado. Ver
[`tests/fixtures/README.md`](../tests/fixtures/README.md).

### 3. Un modelo respondió desde el sensor (2026-08-30)

A Claude Desktop, configurado con `INDUSTRIAL_MCP_ADAPTER=esp32` sobre stdio,
se le preguntó *"¿cómo está la temperatura de los silos?"* y respondió:

> Silo-3 (módulo banco-silo3) — Promedio: 27.25 °C · Mín: 26.5 °C / Máx: 28 °C
> · 2 cables, 2 sensores, todos válidos, ninguno en falla · Sin alertas activas

Verificado en cruce contra el dispositivo en el mismo minuto, **fuera** de la
ruta MCP:

```
GET /api/probe?i=0 → {"estado":"valid","temp":26.5,"rom":"2839a47997140315"}
GET /api/probe?i=8 → {"estado":"valid","temp":28,  "rom":"28142007d6013cf6"}
```

El mínimo y el máximo que dijo el modelo son las dos sondas físicas, uno a
uno. Log del host:

```
14:20:58 [LocalMcpServerManager] Connecting to industrial
14:21:07 [LocalMcpServerManager] Connected to industrial (6 tools)
14:21:07 [localMcpBridge] announcing industrial: 6 tool(s)
```

### 4. La compuerta de seguridad sostuvo un pin físico (2026-08-30)

El firmware 0.4.0 agregó `/api/relay`: `POST` maneja GPIO5, `GET` reporta el
nivel **leído de vuelta del pad**, junto con la orden que recibió y un flag
`mismatch`. El RGB integrado lo refleja para la cámara — verde encendido, rojo
apagado — y queda deliberadamente fuera de la verificación, porque un WS2812B
es de solo escritura y no se puede leer.

Las tres capas, corridas en orden contra ese pin:

| Paso | Resultado | Pin después |
|---|---|---|
| 1. `trigger_motor_action` con defaults | `phase: dry_run` | `off` |
| 2. `dry_run=False`, sin `operator_id` | `phase: rejected` | `off` |
| 3. operador + razón, servidor en solo lectura | `phase: rejected_server_policy` | `off` |
| 4. las cuatro condiciones cumplidas | `phase: executed` | **`on`** |

y una línea en el audit log, solo para la llamada que sí ocurrió:

```json
{"ts": 1788144400.76, "actor": "op-42", "action": "motor.start",
 "target": "fan-1", "outcome": "applied",
 "details": {"reason": "demo tres capas", "warnings": []}}
```

**La regla consultiva disparó contra una temperatura medida.** Pedir *detener*
el ventilador regresó:

```
silo silo-3 is at 28.0°C — stopping fan may allow temperature rise;
recommend operator review
```

Esa regla existe desde el primer commit. Hasta ahora el número que traía salía
de un hash; el 28.0 salió de un DS18B20.

**Lo que el adapter se niega a hacer:** `apply_motor_action` decide el éxito
leyendo el pin, no porque el módulo acepte el POST. Una respuesta 200 con el
pin todavía en bajo regresa `ok: False` con `state: "fault"` — decirle a un
operador que un ventilador está corriendo mientras el grano se sigue
calentando es la versión física de promediar un sensor muerto como 0 °C.

### 4b. Y después lo accionó un modelo, no un script (2026-08-30, 20:22–20:23)

La corrida de arriba fue un script llamando a la capa de tools. Esta fue Claude
Desktop decidiendo hacerlo, por stdio, con un humano pidiéndoselo en español.
Tres ciclos, y el LED siguió cada uno dentro del round trip de HTTP:

```
20:22:40  motor.stop   fan-1  applied  actor=brayan  reason: prueba del relé
          WARNING: silo silo-3 is at 28.0°C — stopping fan may allow
                   temperature rise; recommend operator review
20:23:03  motor.start  fan-1  applied  actor=brayan  reason: fin de prueba del relé
20:23:35  motor.stop   fan-1  applied  actor=brayan  reason: paro manual solicitado
          WARNING: silo silo-3 is at 28.0°C — …
```

Dos cosas de ese log valen más que el hecho de que haya funcionado.

**La advertencia quedó en el registro, no solo en la pantalla.** Los dos paros
cargan el aviso de 28.0 °C hasta `audit_industrial.jsonl`. Un post-mortem que
lea esto puede ver que al operador se le dijo que el grano estaba caliente y
paró el ventilador de todas formas — que es justo la razón por la que las
advertencias son datos y no prosa.

**El primer `stop` se negó por el motivo correcto.** Cuando le pidieron parar el
ventilador sin credenciales, el modelo corrió el dry run, reportó la
advertencia, y regresó pidiendo un id de operador y una razón antes de seguir.
Nadie le dijo que tuviera cuidado; el contrato de la herramienta hizo que el
camino cuidadoso fuera el único disponible. Así se debe sentir la compuerta
desde afuera.

### 4c. Una falla a la que el software no le pudo dar la vuelta con palabras (2026-08-31)

El firmware 0.6.0 divide el actuador en dos pines: GPIO5 acciona, GPIO6 lee si
lo siguió. `state` sale de la realimentación, `drive` del pin de salida, y
conservar ambos es lo que vuelve la falla diagnosticable y no solo detectable.

Con el cable de realimentación desconectado, y orden de encender:

```json
{"id":"fan-1","commanded":"on","drive":"on","state":"off",
 "pin":5,"pin_fb":6,"mismatch":true}
```

`drive: "on"` dice que el GPIO está haciendo su trabajo. `state: "off"` dice
que el actuador no. Un solo pin no habría podido distinguirlos.

**Y luego la parte interesante.** Llamada con
`INDUSTRIAL_MCP_ALLOW_WRITES=true`, `dry_run=False`, un `operator_id` y una
`reason` — todas las compuertas abiertas — la herramienta aun así regresó:

```
phase        : blocked_by_safety
would_execute: False
no_active_fault   passed=False   fault must be cleared first
action_meaningful passed=False   start requested while motor is fault
```

Nada se negó por un permiso faltante. Se negó porque el hardware dijo que el
actuador estaba en falla, y esa verificación nunca había corrido ni una sola
vez contra un hecho físico.

**Y la falla se limpió sola.** Reconectar el cable movió `mismatch` de `true`
a `false` sin mandar ningún comando — el estado se lee, no lo enclava un
software que luego necesitaría que alguien se acordara de resetearlo.

La matriz sana, corrida de principio a fin sobre el mismo actuador:

| Paso | `phase` | cmd | drive | realimentación |
|---|---|---|---|---|
| 1. defaults | `dry_run` | off | off | off |
| 2. sin `operator_id` | `rejected` | off | off | off |
| 3. servidor en solo lectura | `rejected_server_policy` | off | off | off |
| 4. las cuatro condiciones | `executed` | **on** | **on** | **on** |
| 5. stop, todas las condiciones | `executed` | off | off | off |

### 5. Las negativas se sostienen cuando el módulo está ciego

Sin sonda conectada, el mismo adapter regresa lo contrario, y eso también es
correcto:

```json
{"cable_count": 0, "sensors_total": 0, "error": "no_valid_readings",
 "min_temp_c": null, "max_temp_c": null, "avg_temp_c": null}
```

más una alerta, porque el silencio y un silo fresco se ven idénticos para un
consumidor:

```json
[{"kind": "sensor_health", "severity": "warning",
  "detail": "0 sensor(s) seen on cable(s) [0], none classified valid by the module"}]
```

En la misma sesión al modelo se le dijo que `capacity_t` y `fill_pct` son
`null` y se lo pasó al usuario como *"eso tendrías que consultarlo en otro
lado"* — el módulo mide temperatura de grano, no nivel, y un `0` ahí se
leería como un silo vacío.

---

## Transporte y exposición

Esto es MCP sobre **stdio**. El host MCP lanza el servidor como proceso local
y le habla por stdin/stdout. Nada llega al módulo desde fuera de la LAN, y
ningún proveedor lo toca.

Eso importa porque **el módulo no tiene autenticación**. `POST /api/config`
edita NVS sin credencial alguna. Es aceptable en una LAN de laboratorio para
una demo y no es aceptable en una planta. Si esto llegara a exponerse de forma
remota, se le pone un gateway autenticado enfrente — no al módulo.

El adapter es de solo lectura por construcción: llama tres endpoints GET y
nada más.

---

## Lo que esto le costó a la reputación del firmware, honestamente

Del trabajo de banco salieron dos hallazgos que recaen sobre los dispositivos,
no sobre este repo. Ambos quedan registrados porque son el tipo de cosa que se
elimina en silencio de una historia de éxito.

### Hacer polling demasiado rápido saca al módulo de la red

Sondear ambos cables **una vez por segundo** mató el servidor HTTP en unos 15
segundos, y no se recuperó solo — 60 segundos de `curl` regresaron timeouts
mientras la tarjeta **seguía respondiendo ping**. Solo un reset lo recuperó.
Esa combinación es lo que lo hace engañoso: parece una falla de red y es
firmware.

`/api/probe` corre la lectura OneWire dentro del callback de AsyncTCP
(`web_server.h:80-91`). `probeCable` → `owScanCable` → `delay(dsConvMs())`, y
`dsConvMs()` es 125 ms a resolución de 9 bits. Dos cables por muestra son un
cuarto de segundo de `delay()` duro por segundo dentro de la task que además
tiene que atender TCP; la cola de eventos se acumula y nunca se vacía. lwIP
responde ICMP desde otra task, y por eso el ping sobrevive.

El propio comentario de cabecera del firmware enuncia la regla que rompió:

> *"El barrido sigue siendo diferido: `/api/scan` marca el flag y el lazo del
> modo config lo corre (**nunca dentro del handler async**)."*

`/api/scan` la respeta — marca un flag y regresa 202. `/api/probe`, agregado
después para lecturas en vivo, no heredó la defensa.

**Mitigación en este repo hoy:** no hacer polling más rápido que una vez cada
5 segundos. Verificado estable durante 60 segundos continuos a ese ritmo con
2/2 sensores válidos. **El arreglo real pertenece al firmware** — diferir el
probe igual que se difiere el barrido.

La parte que vale la pena conservar: durante toda la caída el adapter reportó
`device_unreachable` y nunca inventó una temperatura. Pero un consumidor que
hace polling agresivo **puede dejar ciego al equipo que está monitoreando**, y
en un silo real eso es ceguera autoinfligida. El rate limiting va en el
adapter, no en la disciplina de quien lo llame.

### El otro firmware de la misma familia falla en silencio

`agrostar-s3-onewire` hace lo honesto a nivel dispositivo, con más cuidado del
que este repo le pidió: `classifyChannel` separa `CH_EMPTY` / `CH_BAD_CRC`
/ `CH_OUT_OF_RANGE` / `CH_OK`, solo `CH_OK` produce una temperatura, y atrapa
específicamente el 85.00 °C exacto — el valor de power-on-reset del scratchpad
del DS18B20, que pasa el CRC y de otro modo se leería como una medición buena
de un sensor que nunca completó una conversión.

`agrostar-termometria`, el firmware para el que se escribió originalmente este
plan, hace lo contrario: genera 133 valores mock y sobrescribe solo los que el
MAX31856 leyó limpio (`main.cpp:21-46`). **En caso de falla el valor mock se
queda** — un ~24 °C plausible, indistinguible de una medición. Si ese firmware
llega a usarse, necesita un flag `real` por sensor en `readingToJson` antes de
que se pueda confiar en un adapter contra él.

---

## Sin verificar

Cosas que se creen pero no se demostraron. Están separadas aquí a propósito.

**Un relevador no es un motor.** GPIO5 maneja un pin de nivel lógico y GPIO6
lo lee de vuelta; el LED integrado refleja el estado. No se ha conmutado nada
con inercia, corriente de arranque o un térmico de sobrecarga. La
realimentación de marcha ya es genuinamente parte del diseño y no un hueco —
pero secuenciar un ventilador trifásico sigue trayendo interlocks y requisitos
de categoría de paro que nada de esto atiende.

**Una etapa de salida muerta sigue siendo construida.** El adapter distingue
"el actuador no siguió" de "el GPIO nunca accionó", y solo la primera se ha
producido en hardware. Reproducir la segunda implica dañar un pin, así que esa
rama se queda como un payload construido — el único que queda en este repo.

**Solo existe un actuador.** `RELAY_ID` es una constante en tiempo de
compilación, así que el módulo responde exactamente por `fan-1`. Múltiples
relevadores necesitarían que el id se volviera un lookup, y `POST` hoy regresa
404 para cualquier otra cosa — que es la falla correcta, pero no es una
flotilla.

**Un bus aterrizado puede reportar 0.0 °C como válido.** Leído del código, no
medido. `owReadSensor` acepta un scratchpad cuando `crc8(sp,8) == sp[8]`. Un
corto a tierra sostenido muestrea cada bit como 0, y el CRC-8 Dallas sobre
ocho bytes en cero es cero — que es lo que queda en `sp[8]`. El CRC pasaría,
`decodeScratchpadTemp` regresaría `0.0`, y `classifyChannel` lo aprobaría: no
es NaN, está dentro de −50..125, no es 85.00. Saldría como
`estado: "valid", temp: 0`.

Un intento de confirmarlo el 2026-08-30 no logró reproducirlo — 90 segundos de
polling dieron 394 lecturas `valid` entre 26.0–26.5 °C y ningún cero, porque
el corto no se sostuvo durante la ventana. **Sin demostrar, no refutado.** Una
hipótesis rival por descartar: que `g_ow.reset()` no vea pulso de presencia
con la línea aterrizada y aborte antes de leer.

Si se confirma, el arreglo no requiere adivinar temperaturas: un scratchpad
genuino nunca es puro cero. El byte 4 es el registro de configuración (`0x1F`
a 9 bits), el byte 5 es reservado `0xFF`, el byte 7 siempre es `0x10`. Validar
uno de esos antes de aceptar el CRC manda el `0.00` fabricado a `CH_BAD_CRC`,
que es donde pertenece.

Esto le importa más a la flotilla desplegada que a este repo: un cable de
termometría que se aterrice en una caja de conexiones de un silo hoy
reportaría 0 °C con cara de datos buenos, y el grano caliente promedia hacia
abajo.

---

## Cómo reproducir esto

```bash
INDUSTRIAL_MCP_ADAPTER=esp32 \
INDUSTRIAL_MCP_ESP32_HOST=banco-silo3.local \
INDUSTRIAL_MCP_ESP32_DEVICE_ID=banco-silo3 \
INDUSTRIAL_MCP_ESP32_CABLES=0,8 \
uv run python -c "
from industrial_mcp.adapters.esp32 import Esp32Adapter
print(Esp32Adapter.from_env().get_silo_thermometry('silo-3'))
"
```

`INDUSTRIAL_MCP_ESP32_CABLES` por defecto es solo el canal `0`. Omitirlo
descarta en silencio todas las demás sondas — cada canal cuesta una conversión
OneWire, así que el default es barato, no completo.

Para un host MCP, ver [`examples/claude-desktop-config.json`](../examples/claude-desktop-config.json).

**La dirección se mueve, así que deja de usar una.** Este módulo tomó cuatro
leases DHCP distintos en dos días (`.71` → `.69` → `.100` → `.66`), y el último
pasó **sin reiniciarse**: simplemente renovó. Cada config obsoleta en la
historia de este proyecto sale de ahí.

El firmware 0.5.0 publica `<device_id>.local` por mDNS y lo re-anuncia al
reconectar, así que el nombre sigue al dispositivo. El salto a `.66` se detectó
justo así: la dirección nunca se consultó. Pon también
`INDUSTRIAL_MCP_ESP32_DEVICE_ID` y el adapter se relocaliza a media sesión si el
nombre llegara a fallar — adoptando solo un dispositivo que se identifique con
ese id, porque un barrido no puede distinguir "mi módulo se movió" de "contestó
otra cosa".

Reservar el lease por MAC sigue valiendo la pena. Solo que ya no es lo único
que te separa de una config rota.
