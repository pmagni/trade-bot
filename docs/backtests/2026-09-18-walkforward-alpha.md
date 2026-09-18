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

### 2.3 El techo no es el take-profit, es el trailing

Ganadores: p90 = +4.30%, máximo absoluto +7.82% en 18 meses. Correr el
backtest con `take_profit_pct = 0` da **exactamente el mismo resultado** en las
tres ventanas: el trailing (activa a +1.5%, distancia 0.8%) corta antes que el
TP en casi todos los casos. El techo efectivo por trade es ~+2.3%.

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

Robustez en 6 ventanas de 3 meses: la config propuesta gana a producción en
**5 de 6**.

Sensibilidad (descarta óptimo de filo): buy score 5 → +13.93%, **6 → +16.61%**,
7 → +5.10%. El 6 no es un pico ajustado: es el borde entre trades que no
aportan nada y trades que aportan todo. Los 53 trades score-5 de 18 meses
suman **−$0.50** de P&L. Los 115 score-6 suman +$54.

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
de muestra en dos ventanas y en 5 de 6 sub-ventanas.

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

1. **Datos reales de la tabla `trades`.** Este diagnóstico usa el backtest
   (268 cierres). La DB local está vacía y la de producción
   (`root@159.223.8.250`, ~222 cierres) no fue accesible: la clave SSH no está
   cargada. Correr `ssh-add ~/.ssh/id_ed25519` y repetir §2 sobre datos reales.
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
