# Dónde se pierde el alpha — walk-forward 3 ventanas

Fecha: 2026-09-18 · Rama: `exp/alpha-walkforward` · Dataset: `data/`
(BTCUSDT+ETHUSDT+BNBUSDT, 4h, 3373 velas, 2025-03-01 → 2026-09-14) ·
Harness: `walkforward.py` (nuevo) sobre `backtest.py`.

## 0. La premisa de partida estaba mal

El README dice "combo +7.17% vs baseline +7.20%" y eso se venía leyendo como
"empatamos con buy & hold". **`baseline` en ese harness es la config v2.14, no
buy & hold.** `backtest.py` nunca calculó buy & hold: no existía la función.

Con buy & hold real, equiponderado, mismas fees y misma ventana:

| | retorno | maxDD |
|---|---|---|
| Buy & hold 100% | **+10.81%** | **60.5%** |
| Producción hoy (COMBO) | +5.77% | 10.2% |

No es un empate: son 5 puntos abajo. Pero con 1/6 del drawdown, lo que hace
que la comparación directa no signifique lo que parece — ver §4.

## 1. Las tres ventanas son tres mercados distintos

| ventana | periodo | buy & hold | maxDD B&H | producción | delta |
|---|---|---|---|---|---|
| W1 | 2025-03-17 → 2025-09-15 | **+71.63%** | 20.7% | +5.32% | **−66.31pp** |
| W2 | 2025-09-15 → 2026-03-16 | **−37.48%** | 55.2% | −0.63% | **+36.85pp** |
| W3 | 2026-03-16 → 2026-09-14 | +6.43% | 27.3% | +1.87% | −4.56pp |

El agregado de 18 meses (−5pp) es el promedio de −66 y +37. **No describe
ningún régimen real.** El bot es de beta muy baja: no participa del alza ni de
la baja. Esa es la propiedad estructural, y el resto del diagnóstico se explica
desde ahí.

## 2. Diagnóstico por trade (268 cierres, config de producción)

### 2.1 Dónde está el P&L

| razón de salida | n | P&L | $/trade | hold mediano |
|---|---|---|---|---|
| take_profit | 46 | **+$357.51** | +7.77 | 24h |
| trailing_stop | 25 | +$89.02 | +3.56 | 36h |
| signal_sell | **165** | −$32.41 | −0.20 | 76h |
| stop_loss | 32 | **−$291.30** | −9.10 | 28h |

62% de las salidas (`signal_sell`) aportan P&L ≈ 0. El sistema vive de
take_profit y muere en stop_loss.

### 2.2 La hipótesis del holding period es circular

La tabla cruda confirma el comentario de `check_stop_loss` — holds de 4-24h
dan +$182 y los de 96h+ dan −$26. Pero cortando por razón de salida:

| banda (h) | n | %take_profit | %signal_sell |
|---|---|---|---|
| 0-12 | 19 | **58%** | 16% |
| 48-96 | 100 | 9% | 80% |
| 96+ | 63 | 5% | **92%** |

La banda corta es rentable **porque está hecha de take-profits**, que por
construcción solo cierran en ganancia y rápido. Dentro de `signal_sell` sola
el patrón se desarma (0-12h: −$1.33/trade). El holding period no es causal:
es el residuo de la regla de salida. **Hipótesis 1: refutada como causa.**

> **Revocado por §2.4.** Este veredicto no sobrevivió al contraste con
> producción. El control que lo sostiene — `signal_sell` sola — está deformado
> en el harness, que estira el hold mediano de esa salida de 16h a 76h. Sobre
> datos reales el patrón sí se mantiene dentro de `signal_sell`
> (p = 0.003). H1 vuelve a estar abierta.

### 2.3 El techo por trade es bajo — y el take-profit es lo que lo sostiene

Ganadores: p90 = +4.30%, máximo absoluto +7.82% en 18 meses. El techo efectivo
por trade es ~+2.3%: esta estrategia no tiene trades grandes, tiene muchos
trades chicos que cierran rápido.

La pregunta natural es cuál de las dos reglas de salida impone ese techo — el
take-profit de +3% o el trailing (activa a +1.5%, distancia 0.8%). La respuesta
es el take-profit, y no por poco. Corriendo con `take_profit_pct = 0` y el
trailing intacto, sobre los 18 meses completos:

| | retorno | maxDD | PF |
|---|---|---|---|
| producción | **+5.77%** | 10.2% | **1.33** |
| sin take-profit (`notp`) | **−4.09%** | 11.9% | 1.06 |

Casi 10 puntos de diferencia. **El TP de +3% es load-bearing**: no es un techo
inerte que el trailing vuelve irrelevante, es la regla que convierte la
estrategia en rentable. Sin él, el trailing suelta las ganancias chicas antes
de que se consoliden y el sistema queda en pérdida.

> **Corrección (revisión posterior).** Una versión anterior de esta sección
> afirmaba lo contrario — que quitar el TP "da exactamente el mismo resultado"
> y que el techo lo ponía el trailing. El error fue de comparación: la fila
> `notp` es idéntica a `tprun` (la variante donde el TP arma el trailing en vez
> de vender), **no** a producción. Se cotejó contra la fila equivocada. La
> conclusión correcta es la inversa, y es la que queda arriba.

Esto no cambia ninguna recomendación de este informe — la propuesta de §4 no
toca el take-profit — pero sí refuerza §3: sobre una entrada de reversión, el
objetivo tiene que ser corto y duro. Aflojarlo, de cualquiera de las formas
probadas, empeora el resultado.

### 2.4 Contraste con producción real — H1 vuelve a estar viva

Datos: tabla `positions` de producción, 229 cierres entre 2026-04-01 y
2026-09-18 (5.5 meses, posición mediana $10.02, spot puro — `leverage = 1.0`
en las 229). Es la verificación que §7.1 dejaba pendiente. **No confirma §2.1
ni §2.2.**

| razón | n | %/trade real | hold med real | hold med backtest |
|---|---|---|---|---|
| take_profit | 15 | +3.16% | 18h | 24h |
| trailing_stop | 7 | +2.10% | 30h | 36h |
| stop_loss | 18 | −5.08% | 25h | 28h |
| **signal_sell** | **164** | **+0.71%** | **16h** | **76h** |

PF real 1.23, win rate 64%, P&L +$7.12 sobre 229 trades.

**Dos divergencias con el backtest, y son la misma.** Primero: en el harness
`signal_sell` es un lastre de P&L ≈ 0 (−$0.20/trade); en producción es el
principal aportante (+0.71%/trade, 66% de aciertos). Segundo: su hold mediano
es 16h real contra 76h simulado — **4.75x**. Las otras tres salidas coinciden
dentro del 25%, así que el harness no está mal en general: está mal
exactamente en la salida por score, que es el 72% de los cierres.

La causa es conocida y ya estaba documentada como límite del harness: el
backtest evalúa señales solo al cierre de velas 4h, mientras el bot escanea
cada 5-15 minutos. Las salidas por regla (TP, trailing, stop) disparan
intra-vela y por eso coinciden. La salida por score solo puede dispararse en
el tick de las 4h, así que el simulador la retrasa sistemáticamente.

**Consecuencia sobre la hipótesis 1.** §2.2 la declaró "refutada como causa"
porque, dentro de `signal_sell` sola, el patrón de holding period se
desarmaba. Sobre datos reales no se desarma:

| `signal_sell` sola | n | %/trade |
|---|---|---|
| < 24h | 104 | **+0.89%** |
| ≥ 48h | 37 | **−0.08%** |

Diferencia 0.97pp, test de permutación **p = 0.003**. El efecto sobrevive al
control que lo mataba en simulación — y lo mataba porque el harness comprime
todos los holds cortos hacia arriba, hasta 76h de mediana. **H1 vuelve a estar
abierta**: sobre datos reales, los holds largos por señal sí destruyen valor.

Lo que esto NO autoriza es reinstalar el time-stop: la variante `time_stop_48h`
se probó y fue negativa (§3), pero se probó *en el harness*, o sea contra la
misma distribución de holds deformada. Ese experimento hay que rehacerlo con un
simulador que vea ventanas menores a 4h, o directamente en canario.

**Alcance.** 5.5 meses contra los 18 del backtest, un solo régimen, y P&L total
de $7.12 — los porcentajes son sólidos, los dólares son ruido de tamaño de
posición. No invalida §4: la calibración de scores no depende de esto. Sí
degrada la confianza en cualquier conclusión del informe que dependa del
*timing* de `signal_sell`.

## 3. Lo que NO funciona (probado, no asumido)

| cambio | hipótesis que atacaba | W1 | W2 | W3 | veredicto |
|---|---|---|---|---|---|
| break-even a +1.0% | H3 (asimetría salida) | −0.97% | −5.71% | −0.39% | **peor en las 3** |
| time-stop duro 48h | H1 (holds largos) | +2.20% | −3.84% | +3.75% | mixto, negativo neto |
| TP arma trailing | H2 (dejar correr) | +4.47% | −7.27% | −0.22% | **peor en las 3** |
| trailing ancho 3%/2.5% | H2 | +2.43% | −14.27% | −1.78% | **muy peor** |
| trailing ancho 5%/4% | H2 | +3.22% | −14.27% | +1.10% | **muy peor** |
| *(producción)* | — | +5.32% | −0.63% | +1.87% | referencia |

**El hallazgo más importante de esta tabla:** ensanchar la salida para
participar de la tendencia empeora todo, en todos los regímenes. La razón es
que las entradas son de reversión a la media (RSI bajo, bajo banda inferior de
BB, caída desde máximo de 14d). Una entrada de reversión no tiene excursión
favorable larga: al ensanchar el objetivo, las ganancias chicas se convierten
en stop-losses. **No se puede poner una salida de tendencia sobre una entrada
de reversión.** Eso invalida la vía directa hacia el objetivo original.

El break-even también falla, y confirma por otro camino la nota ya documentada
sobre el trailing ("dejar respirar bajo la activación"): las posiciones que
vuelven a la entrada se recuperan con frecuencia suficiente como para que
cortarlas planas cueste plata.

## 4. Lo que SÍ funciona

Protocolo: candidatos elegidos mirando **solo W1**; W2 y W3 son validación
fuera de muestra. Los tres componentes de abajo eran los mejores de W1 en la
primera pasada, antes de mirar W2/W3.

| variante | W1 (calib.) | W2 (valid.) | W3 (valid.) | 18m completo | maxDD | PF |
|---|---|---|---|---|---|---|
| producción | +5.32% | −0.63% | +1.87% | +5.77% | 10.2% | 1.33 |
| `min_buy_score=6` | +12.52% | −1.15% | +3.23% | +13.93% | 10.5% | 1.55 |
| `+ sell_partial=6` | **+13.86%** | **+0.14%** | **+3.42%** | **+16.61%** | **10.4%** | **1.61** |
| + sin reentry | +13.17% | −0.50% | +4.06% | +16.37% | 10.6% | 1.63 |

Robustez en 6 ventanas de 3 meses (`2026-09-18-sweep-6ventanas.txt`): la
config propuesta gana a producción en **4 de 6**. Pierde en W6 (−0.97% vs
−0.07%) y empata técnicamente en W3 (+1.55% vs +1.57%, 0.02pp). Nota: el
**5 de 6** que figuraba antes acá correspondía a `ms6_sg6_nr` — la variante
con reentry desactivado, que no es la que se recomienda. El artefacto ahora
corre las tres filas (`prod`, `ms6_sg6`, `ms6_sg6_nr`) en la misma pasada.

**Sensibilidad, un eje por vez** (18m, `2026-09-18-sensibilidad-scores.txt`).
Una versión anterior de este párrafo mezclaba los dos ejes en una sola línea
rotulada "buy score"; los valores que citaba eran mitad de un barrido y mitad
del otro. Corridos por separado:

| buy score (sell gate fijo en 6) | 18m | | sell gate (buy score fijo en 6) | 18m |
|---|---|---|---|---|
| 5 | +7.03% | | 5 | +13.93% |
| **6** | **+16.61%** | | **6** | **+16.61%** |
| 7 | +5.10% | | 7 | +14.93% |

El sell gate se porta bien: la meseta 5-7 va de +13.93% a +16.61%, así que el 6
es una preferencia, no una dependencia.

**El buy score no.** Los vecinos del 6 valen menos de la mitad (+7.03% y
+5.10%): es un pico pronunciado, y hay que decirlo — es la fragilidad principal
de esta propuesta, no un detalle. Lo que la sostiene no es la forma de la curva
sino que cada lado tiene una explicación mecánica distinta: por abajo, los 53
trades score-5 de 18 meses suman **−$0.50** de P&L contra +$54 de los 115
score-6 — es churn puro, y filtrarlo es lo que produce el salto; por arriba, el
7 no falla por mala selección sino por inanición, deja tan pocas entradas que
no alcanza a componer. Que ambos lados caigan por razones diferentes hace menos
probable que el 6 sea un artefacto del dataset, pero **no lo descarta**: con un
solo ciclo de datos, un pico de esta forma es exactamente lo que produciría el
overfitting. Antes de operar esto con capital real hay que verlo en otro ciclo.

Nota: el 6 ya tiene precedente independiente — `btc_min_buy_score = 6` existe
desde v2.10, calibrado sobre otros datos y por otra vía.

Nota: `btc_min_buy_score = 6` ya existe desde v2.10 con la misma justificación.
Este cambio extiende a ETH y BNB una regla que BTC ya tenía.

## 5. Contra qué baseline hay que medir

El bot tiene un tope de drawdown de 12%. Buy & hold al 100% tiene 60.5%. No son
comparables. El baseline honesto es buy & hold escalado al **mismo presupuesto
de riesgo**: la fracción `x` del capital en B&H y el resto en USDT que deja el
maxDD exactamente en 12%.

| ventana | B&H 100% | maxDD | x* | **B&H con tope 12%** | producción | propuesta |
|---|---|---|---|---|---|---|
| W1 | +71.63% | 20.7% | 56.9% | **+40.73%** | +5.32% | +13.86% |
| W2 | −37.48% | 55.2% | 19.0% | **−7.11%** | −0.63% | +0.14% |
| W3 | +6.43% | 27.3% | 42.8% | **+2.75%** | +1.87% | +3.42% |
| 18m | +10.81% | 60.5% | 11.0% | **+1.19%** | +5.77% | **+16.61%** |

Sobre el periodo completo y respetando la restricción de 12% que vos mismo
fijaste, producción ya batía al baseline por 4.6pp. La propuesta lo bate por
**15.4pp**. Y contra buy & hold sin restricción de riesgo, la propuesta gana
por **+5.80pp** con 10.4% de drawdown.

Límite honesto: por ventana el cuadro es mixto. En W1 (bull) hasta el baseline
con tope de riesgo saca 27pp de ventaja. El bot gana el periodo completo por
camino, no por ventana: evita W2 y compone desde una base más alta.

## 6. Veredicto sobre el objetivo original

El objetivo — batir buy & hold por 3-5pp con maxDD ≤12% sobre 18 meses — se
cumple con `min_buy_score=6` + `sell_partial=6`: **+16.61% vs +10.81%
(+5.80pp), maxDD 10.4% (< 12%), PF 1.61 (> 1.38-1.40 actual)**, validado fuera
de muestra en dos ventanas y en 4 de 6 sub-ventanas.

Con una salvedad que pesa: el resultado cuelga de un parámetro con pico
pronunciado. Mover el buy score un punto en cualquier dirección corta el
retorno a menos de la mitad (§4). Hay razones mecánicas para que el 6 sea el
lugar correcto, pero sobre un solo ciclo de datos eso no se puede distinguir
de un ajuste al dataset. El veredicto es "se cumple en backtest", no "está
listo para capital real" — para eso falta §7.

Lo que **no** se cumple, y conviene decirlo: el objetivo *narrativo* —
"capturar ciclos alcistas y vender antes de la caída" — no es lo que hace esta
estrategia ni lo que puede hacer con esta arquitectura. En W1 el mercado hizo
+71.6% y el mejor config probado hizo +13.9%. Toda variante que intentó
participar de la tendencia empeoró en las tres ventanas (§3). El alpha de este
bot es **defensivo**: gana el periodo evitando el tramo bajista, no
capturando el alcista. Si el objetivo real es capturar el ciclo, hace falta
otra estrategia — de momentum, con entradas de ruptura, no de reversión — y
eso es un proyecto nuevo, no un ajuste de parámetros.

## 7. Lo que falta antes de ir a producción

1. ~~**Datos reales de la tabla `trades`.**~~ **Hecho — ver §2.4.** Se
   contrastaron los 229 cierres reales de producción (2026-04-01 → 2026-09-18)
   contra los 268 del backtest. Resultado: el harness es fiel en las salidas
   por regla y no lo es en la salida por score, que es el 72% de los cierres.
   Eso revocó el veredicto de §2.2 sobre la hipótesis 1. Lo que ahora falta en
   su lugar: **un simulador con granularidad menor a 4h**, sin el cual no se
   puede evaluar honestamente ninguna variante que dependa del timing de
   `signal_sell` — incluido el time-stop descartado en §3.
2. **Un solo dataset.** 18 meses, un ciclo completo. La conclusión de §3
   (reversión ≠ tendencia) es mecanística y debería generalizar; la calibración
   de §4 no está validada en otro ciclo.
3. **El canario de stops nativos (v2.20) sigue abierto.** No cambiar estrategia
   en simultáneo, por la misma razón documentada en
   `2026-09-14-decisiones-pendientes.md`.
4. `breakeven_trigger_pct`, `time_stop_hours` y `tp_arms_trailing` quedaron en
   `config.py` con default OFF y evidencia negativa registrada acá. El campo
   `hwm` que usa el break-even se mutaría in-place: llevarlo a producción
   exigiría una columna en `positions`.
