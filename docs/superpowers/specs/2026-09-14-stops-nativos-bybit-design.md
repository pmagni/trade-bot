# Stops nativos en Bybit — diseño

Fecha: 2026-09-14 · Versión objetivo: v2.20 · Estado: aprobado, sin implementar

## Problema

Todos los stop-loss del bot son del lado del bot: `_check_stop_losses` compara
el precio en cada scan y manda una orden de venta. No hay ninguna protección
en el exchange. La vigilancia de riesgo depende de que el proceso esté vivo.

Esa es la causa del costo del blackout de 2026-06-04→06. El bot estuvo 55h
muerto; dos posiciones ETH con stops en 1693.95 y 1652.74 quedaron sin
vigilancia y se liquidaron al revivir a 1550.62: −13.0% y −10.9%, −$4.53, cerca
del 80% del PnL neto histórico. Ver
`docs/superpowers/specs/2026-09-13-v2.18-deadman-alert-design.md`.

v2.17.1 (systemd `Restart=always`) y v2.18 (deadman) atacan la *detección*. Esto
ataca la *exposición*: que la protección sobreviva a la muerte del proceso.

## Alcance

Una orden condicional de venta en Bybit por cada posición abierta, colocada
**por debajo** del stop del bot, que actúa solo si el bot no está.

Fuera de alcance: take-profit y trailing siguen siendo del bot. Son dinámicos y
el trailing tiene una regla de activación deliberadamente contraintuitiva, ya
backtesteada (ver el comentario en `strategy.check_stop_loss`). No se tocan.

## Decisiones tomadas

**1. Red de seguridad, no espejo.** El stop nativo va por debajo del stop del
bot. Con el bot vivo sale él primero y el comportamiento es idéntico al de hoy:
cero impacto sobre lo backtesteado. Un espejo exacto cambiaría los resultados
—el nativo dispara intra-vela, el bot solo cada 15 min de scan— y abriría una
carrera entre ambos.

**2. `orderFilter="StopOrder"`, nunca `tpslOrder`.** De la doc v5 de Bybit:
`tpslOrder` ocupa los activos *antes* de dispararse; `StopOrder` no los ocupa
hasta el trigger. Con `tpslOrder` el bot no podría vender (balance insuficiente)
y el diseño entero se rompe. Límite del exchange: 30 órdenes condicionales
abiertas por símbolo por cuenta — tenemos unas pocas posiciones, sobra.

**3. Reconciliador declarativo, no event-driven.** En cada scan se calcula el
estado deseado desde la DB, se lee el real desde el exchange, y se converge. Es
idempotente y auto-curativo: un crash, un restart o un evento perdido se
corrigen solos en el siguiente scan. Colapsa "reconciliación al arranque" y
"mantenimiento en runtime" en un solo camino.

**4. Rollout por canario.** Flag por símbolo. Semana 1 BNBUSDT (menor
exposición, ~$13/posición), semana 2 +ETHUSDT, semana 3 +BTCUSDT.

## Componentes

### `native_stops.py` (nuevo)

Núcleo puro más cáscara imperativa, siguiendo el patrón de `sell_rules.py`.

```python
@dataclass(frozen=True)
class DesiredStop:
    link_id: str        # "nsl-{position_id}"
    symbol: str
    qty: float
    trigger_price: float

@dataclass(frozen=True)
class ReconcilePlan:
    to_place: list      # list[DesiredStop]
    to_cancel: list     # list[str] — order_ids del exchange

def desired_stops(positions: list, prices: dict, margin: float) -> dict: ...
def reconcile_plan(desired: dict, actual: list) -> ReconcilePlan: ...
def apply(plan: ReconcilePlan) -> None: ...        # única parte con I/O
def detect_external_closes(symbol: str) -> list: ...
```

`desired_stops` y `reconcile_plan` son puras y llevan tests. `apply` y
`detect_external_closes` son la cáscara.

### `exchange.py` — cuatro wrappers nuevos

```python
def place_spot_stop_order(symbol, qty, trigger_price, link_id) -> dict
def cancel_order(symbol, order_id) -> dict
def get_open_stop_orders(symbol) -> list
def get_filled_stop_orders(symbol, link_id_prefix="nsl-", lookback_hours=168) -> list
```

`lookback_hours` por defecto 168 (7 días): tiene que cubrir el peor blackout
plausible con margen. El de junio duró 55h. Bybit limita el historial de órdenes
spot a los últimos 7 días, así que 168h es también el máximo disponible — si un
blackout superara eso, la posición se detecta igual por el faltante de balance,
pero habría que cerrarla a mano porque no se puede recuperar el precio de fill.
Ese caso se loguea en ERROR y se avisa por Telegram en vez de adivinar un precio.

Deliberadamente finos: traducción de parámetros y nada de lógica, porque esta
capa no va a tener tests (ver Testing).

`place_spot_stop_order` usa `category="spot"`, `side="Sell"`,
`orderType="Market"`, `orderFilter="StopOrder"`, `triggerPrice`, y
`orderLinkId=link_id`.

### `config.py`

```python
@dataclass
class NativeStopsConfig:
    enabled_symbols: list = field(default_factory=list)   # canario: []
    margin_pct: float = 0.015    # el nativo va 1.5% bajo el stop del bot
```

### `bot.py`

Dos llamadas al reconciliador, ambas a la misma función:
- en `scan_cycle`, **antes** de la lógica de venta
- en `_execute_buy`, justo después de registrar la posición

### `database.py`

Sin cambios de schema. Los cierres externos usan el `close_position` existente
con `close_reason="native_stop"`.

## Reglas del núcleo

### Precio

```
trigger = position.stop_loss × (1 − margin_pct)
```

Derivado del `stop_loss` de la posición, **no** de `entry × −5%`. El stop del bot
ya varía según el régimen: 3.5% normal, 2.5% en uptrend
(`uptrend_stop_loss_pct`), 5.6% en downtrend (3.5% × `downtrend_stop_multiplier`
1.6). Un porcentaje fijo desde la entrada dejaría el nativo *por encima* del
stop del bot en downtrend, y dispararía primero.

El trailing sube por encima de `stop_loss`; el nativo se queda en el piso
original. Es correcto: es una red, no un espejo.

### Invariantes de seguridad

`desired_stops` **no emite** una `DesiredStop` para una posición que no cumpla
las dos condiciones, y loguea en WARNING:

1. `trigger < position.stop_loss` — estrictamente por debajo del stop del bot
2. `trigger < precio de mercado actual` — nunca una orden que dispare al instante

Quedarse sin red es preferible a colocar una trampa que venda una posición sana.

### Convergencia

El vínculo orden ↔ posición es `orderLinkId = "nsl-{position_id}"`, así que la
reconciliación es comparación de conjuntos, sin heurísticas:

| estado | acción |
|---|---|
| deseada, no existe | colocar |
| existe, no deseada | cancelar |
| existe con qty o trigger distinto | cancelar y recolocar |

Cancelar y recolocar en vez de `amend`: una operación menos que puede fallar a
la mitad, y el resultado es idempotente.

**Propiedad clave:** reconciliar un estado ya convergido produce un plan vacío.
Es lo que hace seguro correrlo cada 15 minutos.

### Detección de cierre externo

El mismo loop, porque ya compara DB contra exchange. Si
`balance < suma(qty de posiciones abiertas) − tolerancia`, hubo un cierre que el
bot no registró.

No se adivina cuál: se consulta el historial de órdenes filtrando por
`orderLinkId` con prefijo `nsl-`, lo que da el `position_id` exacto y el precio
de fill real. Se cierra en la DB con ese dato.

La tolerancia es necesaria porque Bybit cobra fees en el activo base y el
balance siempre queda levemente por debajo de lo registrado.

Unidades: el faltante se compara **en USDT**, no en cantidad de base. Es decir
`(tracked_qty − balance) × precio_actual > config.risk.dust_threshold_usdt`. La
comparación tiene que ser en USDT porque `dust_threshold_usdt` lo es, y porque
una misma cantidad de base significa cosas muy distintas en BTC y en BNB.

Este es el hueco que `_sweep_dust` no cubre: solo maneja el caso sobrante
(`balance > tracked`), nunca el faltante.

## Manejo de errores

**Regla de oro:** un fallo en los stops nativos nunca se propaga al loop de
trading. Todo el reconciliador va envuelto en try/except; si la API falla, se
loguea y se reintenta en el próximo scan. Como es idempotente, el reintento es
gratis. Misma regla que `monitoring.heartbeat`.

La alerta a Telegram se manda **al entrar en estado de fallo y al salir**, no en
cada scan: si no, una caída de la API de Bybit generaría un mensaje cada 15
minutos. El estado se guarda en memoria del proceso (un bool por símbolo); tras
un restart se vuelve a avisar una vez, que es el comportamiento deseado.

| modo de falla | mitigación | riesgo residual |
|---|---|---|
| stop colocado demasiado alto | los dos invariantes de `desired_stops` | ninguno si los tests pasan |
| orden huérfana tras venta parcial | el reconciliador la corrige | 1 scan (15 min); benigno — solo dispara bajo el stop original, donde vender es correcto |
| compra sin red hasta el próximo scan | reconciliar tras `_execute_buy` | segundos |
| falso positivo de cierre externo | tolerancia de `dust_threshold_usdt` | ninguno |
| bug que cancela órdenes válidas | degradación al comportamiento de hoy | vuelve al riesgo actual, no peor |
| API de Bybit caída | try/except + reintento idempotente | sin red mientras dure |

**Orden de ejecución:** el reconciliador corre antes de la lógica de venta en el
scan cycle, para que la DB sea verdadera cuando el bot decide. Si no, el bot
podría intentar vender una posición que un stop nativo ya cerró.

## Testing

**Con tests (funciones puras):**
- `desired_stops`: derivación de precio bajo las tres reglas de stop (normal,
  uptrend, downtrend), y los dos invariantes de seguridad
- `reconcile_plan`: colocar, cancelar, recolocar por qty y por trigger, y que un
  estado ya convergido produzca plan vacío
- Mutation testing sobre los dos invariantes, como se hizo con el deadman

**Sin tests, declarado:** los cuatro wrappers de `exchange.py`. El proyecto no
tiene tests para ese módulo, no hay mocks, y no se puede pegar a la API real
desde un test. Se mantienen finos para que la decisión viva en la parte testeada.
Su verificación es el canario manual.

**Canario manual sobre BNBUSDT, checklist:**
1. Tras una compra, la orden condicional aparece en Bybit con el trigger esperado
2. Tras una venta del bot, la orden desaparece
3. Tras una venta parcial, la orden se recoloca con la qty nueva
4. Reconciliar dos veces seguidas no genera actividad (plan vacío)
5. Cierre externo forzado a mano → el bot lo detecta y cierra la posición en la
   DB con el precio de fill real

## Impacto esperado

Sobre el blackout de junio: las dos posiciones ETH tenían stops en 1693.95 y
1652.74 y se liquidaron a 1550.62. Con nativos 1.5% por debajo (≈1668 y ≈1628)
habrían cerrado ahí: aproximadamente −5% cada una en vez de −13.0% y −10.9%. De
los −$4.53, se habrían evitado alrededor de $2.8.

Sobre la operación normal: **ninguno**. Con el bot vivo el stop del bot siempre
dispara primero, por construcción.
