# Auditoría del Swing Bot — Diagnóstico y Oportunidades
**Fecha:** 2026-06-27 · **Versión auditada:** v2.13.1 · **Perspectiva:** analista financiero / quant

---

> **ADENDA 27-jun (datos reales de producción):** tras cargar el historial real (274 trades, 142 posiciones cerradas), el diagnóstico se refina de forma importante. Lee la **§7 — Análisis del historial real**, que tiene prioridad sobre las secciones teóricas §0-§4 donde difieran. Resumen de lo nuevo: (a) el bot **SÍ tomaba ganancias pequeñas** y con eso ganó +14% (abr→may); (b) está **PAUSADO desde el 15-jun** por circuit breaker (drawdown 16.7%) — por eso no opera; (c) el crash de junio borró 6 semanas de ganancias en 13 stop-losses.

## 0. Resumen ejecutivo (TL;DR)

Tu intuición es correcta a medias, y la mitad que falta es la que importa.

- **Matización tras ver datos reales:** el bot **sí cosecha ganancias pequeñas** (116 ventas por señal, mayoría +1.5–2.8%) y con eso subió de $105 a $120 entre abril y mediados de mayo. El movimiento de 2-3% **sí se captura** vía `sell_score`. El agujero real no es la ausencia de salida corta, sino el **riesgo/beneficio negativo** (ganas +1.5%, pierdes −5% a −13%) y la **falta de filtro de régimen**.
- **Por qué "no opera" ahora:** está **`paused` desde el 15-jun** (circuit breaker por drawdown 16.7%) y **no auto-reanuda** — requiere `/resume` manual. Esa es la causa directa de lo que percibes.
- **Matiz crítico que tu hipótesis no contempla:** "comprar la caída y vender el rebote" **pierde dinero en tendencia bajista**. En junio BTC cayó de ~$73K a ~$62K; el bot siguió comprando y comió **13 stop-losses en 6 días** (incl. −13% y −10.9%) que borraron todo. Confirmado con backtest (§3) y con el historial real (§7).
- **La estrategia ganadora** combina **3 piezas**: (1) **filtro de régimen** (no operar contra la tendencia — habría evitado todo el drawdown de junio), (2) **mejor R:R** (stop −2.5% en vez de −5%), (3) auto-reanudar tras circuit breaker con condiciones. Sin la pieza (1), nada más importa.

Veredicto: el bot no es "cauteloso" por exceso de prudencia bien calibrada — es **estructuralmente incapaz de scalping** (sin salida corta) y a la vez **imprudente en bajista** (compra sobreventa en downtrend). Hay que arreglar ambas cosas.

---

## 1. Cómo se movió el mercado (datos reales, 4h, abr–jun 2026)

| Métrica | BTCUSDT | ETHUSDT |
|---|---|---|
| Rango medio por vela 4h | **1.28%** | **1.68%** |
| Vol (std retornos 4h) | 0.87% | 1.14% |
| % velas en sobreventa (RSI<35) | 21.4% | 26.3% |
| % velas en sobrecompra (RSI>70) → **bloquea compra** | 12.3% | 10.7% |
| Oportunidades "dip≥2% → rebote≥1.5%" | **27** (1 cada 3.1 días) | **38** (1 cada 2.2 días) |
| Profundidad media del dip aprovechable | 4.1% | 4.5% |

**Lectura de analista:** el mercado ofreció **~1 micro-swing aprovechable cada 2-3 días**. Material de sobra para una estrategia de "ganancias pequeñas y recurrentes". El problema no es la falta de oportunidades — es que el bot no está diseñado para capturarlas.

---

## 2. Diagnóstico estructural — por qué el bot no hace scalping

### 2.1 No existe salida de ganancia pequeña (causa raíz #1)
La venta requiere `sell_score ≥ 5` (`config.scoring.sell_partial`). Las fuentes de score de venta son:
- RSI > 70 / > 80, %B > 0.95, subida > 8-15% desde mínimo, F&G > 75, ruptura de resistencia.
- Objetivo de ganancia: `profit_target_pct = 0.08` → **+8%** para sumar solo +2 al score.
- Trailing stop.

**No hay ninguna regla "vende si la posición está en +1.5%/+2%".** Un rebote típico de 2-3% sube, toca techo y vuelve a bajar — y el bot sigue dentro esperando una señal de sobrecompra que no llega. Resultado: ganancias no realizadas que se evaporan.

### 2.2 Trailing stop con lógica invertida (bug de diseño)
```
trailing_stop_activation = 1.5%   # se activa al +1.5%
trailing_stop_distance   = 2.0%   # trailing 2% por debajo del pico
```
La **distancia (2%) es mayor que la activación (1.5%)**. Cuando se activa en +1.5%, el stop trailing queda en `pico × 0.98`, que puede estar **por debajo del precio de entrada**. Es decir: una posición que llega a +1.5% y se gira puede cerrarse **en pérdida** por el propio trailing. Matemáticamente no puede "asegurar la ganancia pequeña" que pretende. Para fijar +1.5%, la distancia debe ser < activación (p.ej. activar +1.2%, trail 0.8%).

### 2.3 Objetivo de +8% es de "swing largo", no de scalping
En 83 días, una vela 4h de BTC casi nunca recorre +8% sin antes activar trailing o revertir. El target está calibrado para otra estrategia (swing de varios días). Para "ganancias pequeñas" debe bajar a +1.5–2.5%.

### 2.4 Exceso de "frenos" en el lado de compra
El camino a una compra está lleno de bloqueos acumulativos:
- **Overbought guard:** `RSI > 70 → score = 0` (compra bloqueada del todo). En un rally sano el RSI vive >70 durante días → el bot **nunca compra la tendencia alcista**. Bloquea 12% de las velas, justo las de momentum.
- Bloqueo por cercanía a resistencia (`tolerance 0.8%`).
- Crash velocity (caída >10% en 14d) → freeze 48h.
- **Waterfall: 2 stop-losses en 5 días → 7 días sin comprar** (`consecutive_sl_pause_hours = 168`). En mercado choppy esto congela el bot exactamente cuando aparecen las entradas de reversión.
- Cooldown de 24h tras cualquier stop (`cooldown_after_stop_loss = 1440`).
- BTC exige score ≥6; downtrend exige score ≥6; `min_entry_spread 2%`; score-gate de DCA.

Cada freno individual es defendible, pero **apilados** producen un bot que pasa la mayor parte del tiempo bloqueado. La expansión a SOL/XRP/BNB (commit `0efc7a5`, "while BTC/ETH in no-buy zone") es un síntoma: se añadieron activos porque los principales estaban permanentemente vetados.

### 2.5 Asimetría compra-rápida / venta-lenta
Entra con sondas pequeñas (score 5 = 8% del capital desplegable, score 6 = 28%) pero sin un mecanismo de salida ágil. La combinación "entradas tímidas + salidas lentas" es exactamente el perfil "demasiado cauteloso" que percibes.

### 2.6 Niveles estáticos desfasados
`config.key_levels` (Jun 15) pone el primer soporte BTC en $63K; el precio el 27-jun está en ~$62.7K, **por debajo** de ese soporte → activa "ruptura de soporte" (+score de venta) y no da bonus de compra. El `params_updater` diario sobrescribe esto vía DB, pero el config estático sigue contradiciendo al mercado actual.

---

## 3. Backtest sobre datos reales — evidencia de la tesis

Simulaciones sobre velas 4h reales (Bybit), fee 0.1%/lado, 1 posición a la vez. **Entrada compartida: RSI sobrevendido.** Solo cambia la salida / el filtro.

### 3.1 Salida actual vs. salida-scalp (sin filtro de régimen)
| Símbolo | Salida | Trades | Win% | Hold medio | Retorno compuesto |
|---|---|---|---|---|---|
| BTC | Actual (+8%/OB/trail) | 15 | 47% | ~70h | **−6.4%** |
| BTC | Scalp (+2% TP, −3% stop) | 22 | **59%** | ~33h | −4.8% |
| ETH | Actual | 17 | 35% | ~60h | **−21.3%** |
| ETH | Scalp | 26 | **50%** | ~30h | −25.3% |

La salida-scalp **sube el win-rate y reduce el tiempo en riesgo a la mitad** — pero ambas pierden, porque se compra sobreventa en plena tendencia bajista.

### 3.2 Scalp + filtro de tendencia (solo comprar si precio > EMA50)
| Símbolo | Filtro tendencia | Trades | Win% | Retorno compuesto |
|---|---|---|---|---|
| BTC | **No** | 22 | 55% | −8.9% |
| BTC | **Sí** | 4 | **100%** | **+6.1%** |
| ETH | No | 30 | 57% | −15.6% |
| ETH | Sí | 3 | 33% | −4.9% |

**Conclusión inequívoca:** el filtro de régimen convierte BTC de −8.9% a +6.1%. El número de trades cae porque la ventana fue mayoritariamente bajista (poco tiempo sobre EMA50) — y eso es exactamente lo correcto: **en bajista, no operar.** ETH siguió negativo porque pasó casi todo el periodo en downtrend severo: el filtro lo protegió de operar más (−4.9% con 3 trades vs −15.6% con 30).

> La estrategia de "ganancias pequeñas y recurrentes" es rentable **solo cuando no peleas contra la tendencia**. Es un sistema de **rango/tendencia alcista**, y necesita un interruptor que lo apague en bajista.

---

## 4. Plan de acción — convertirlo en "ganancias pequeñas y recurrentes"

Prioridad por impacto/esfuerzo. Todo es ajuste de `config.py` + una regla nueva de salida en `strategy.py`.

### P0 — Toma de ganancia rápida (la pieza que falta)
Añadir una regla de venta independiente por % de ganancia, escalonada:
```python
# strategy.calc_sell_score(): nueva fuente de score basada en P&L de la posición
#   +1.2% a +2.0%  → score +5 (venta inmediata del tramo scalp)
# Reemplaza el rol de profit_target_pct=8% para el modo scalp.
scalp_take_profit_pct: float = 0.018     # +1.8% dispara salida
scalp_partial_pct:     float = 0.6       # vende 60%, deja 40% de runner
profit_target_pct:     float = 0.05      # baja de 0.08 → 0.05
```

### P0 — Arreglar el trailing (que sí asegure ganancia)
```python
trailing_stop_activation: float = 0.012   # activa antes (+1.2%)
trailing_stop_distance:   float = 0.006   # trail 0.6% (< activación → bloquea ganancia real)
```

### P1 — Filtro de régimen (no pelear la tendencia)
Mantener compras de reversión **solo** si el precio está sobre la EMA media (p.ej. EMA50 4h) o el activo no está en downtrend agudo. Hoy el gate de EMA200 solo *sube el umbral* a 6 pero **igual compra**. Cambiar a: en downtrend confirmado (precio < EMA200 **y** < EMA50 con pendiente negativa) → **no comprar reversión**, solo permitir si hay señal de giro (cruce MACD alcista + RSI saliendo de <30).

### P1 — Suavizar el overbought guard
`RSI>70 → score 0` mata las compras en rally. Cambiar a: no bloquear del todo; restar puntos o permitir compra si hay pullback a soporte dinámico aunque RSI siga alto. Esto te deja participar en tendencias alcistas (donde el scalp long es más seguro).

### P2 — Acortar los frenos para mercado choppy
```python
cooldown_after_stop_loss: 1440 → 360     # 24h → 6h
consecutive_sl_pause_hours: 168 → 48     # 7d → 2d
cooldown_after_buy/sell:    30 → 15
min_hold_minutes:           60 → 30
```
Con stop más ajustado (-2.5%) y TP rápido (+1.8%), las pérdidas son pequeñas y frecuentes; los cooldowns largos dejan de tener sentido y solo apagan el bot.

### P2 — Stop coherente con scalp
```python
stop_loss_pct: 0.05 → 0.025   # -2.5% en uptrend (R:R ~1:0.7 con TP +1.8%)
```
Con TP +1.8% y stop −2.5% necesitas ~58% win-rate para break-even tras fees; el filtro de régimen (§3.2) lleva el win-rate a ~60-100% en régimen favorable. Cuadra.

### P3 — Higiene
- Corregir mensaje stale del overbought guard ("& above upper BB" ya no aplica).
- Reconciliar `config.key_levels` estáticos con el `params_updater` (o eliminar los estáticos y confiar en los dinámicos).
- Revisar `_is_crash_velocity`: usa `drop_from_high` de 14d como proxy de "24h" — congela 48h por caídas viejas. Usar el cierre real de hace 24h.

---

## 5. Riesgos y notas de implementación

- **Cuenta real (mainnet, `BYBIT_TESTNET=false`).** Cualquier cambio mueve dinero real. Recomiendo: implementar en una rama, **validar en testnet o en backtest extendido** (el módulo `v 2.0/backtest.py` existe), y desplegar gradualmente.
- **Fees comen el scalp.** A 0.1%/lado, un round-trip cuesta 0.2%. Un TP de +1.8% deja +1.6% neto — viable, pero confirma tu fee tier en Bybit (maker/VIP baja costos). Si pagas 0.1% taker, evita scalps <1.5%.
- **Régimen lo es todo.** Insisto: este sistema debe **apagarse solo** en bajista. Sin el filtro §3.2 (P1), bajar el TP empeora resultados (§3.1).
- **No pude auditar las transacciones reales de producción** (`root@159.223.8.250:/opt/dcabot/swing_bot.db`): la llave SSH no está cargada en el agente (`Permission denied (publickey)`). La DB local está **vacía** (0 trades/posiciones). Para cerrar el análisis con tu historial real, ejecuta `ssh-add ~/.ssh/id_ed25519` y vuelvo a tirar de los trades reales para contrastar P&L, win-rate y razones de cierre efectivos.

---

## 6. Qué necesito de ti para ejecutar el /goal

1. ¿Autorizas que implemente P0+P1 en una rama (sin desplegar a producción hasta tu OK)?
2. ¿Quieres que valide primero con `backtest.py` sobre 6-12 meses antes de tocar config?
3. ~~Carga la llave SSH~~ ✅ Hecho — historial real incorporado en §7.

---

## 7. Análisis del historial real de producción (274 trades, abr–jun 2026)

Fuente: `root@159.223.8.250:/opt/dcabot/swing_bot.db` copiada el 27-jun. **Cuenta real, mainnet, ~$100 de capital.**

### 7.1 El bot está PAUSADO — esta es la causa directa de "no opera"
```
bot_status   = paused
pause_reason = "Max drawdown 16.7% reached"   (límite config: 12%)
último trade = 2026-06-06 17:55 UTC
hoy          = 2026-06-27   → 21 días sin operar (12 desde el pause formal)
```
El circuit breaker (`max_drawdown_pct = 0.12`) saltó y **no existe lógica de auto-reanudación**. El bot sigue vivo (escanea, actualiza snapshots y niveles dinámicos) pero `scan_cycle` retorna temprano en cada ciclo por `status == "paused"`. Requiere `/resume` manual por Telegram. **Lo que percibes como "demasiado cauteloso" es, literalmente, un bot apagado por seguridad hace 12 días.**

### 7.2 La estrategia FUNCIONABA hasta junio (valida tu enfoque)
| Fecha | Valor portfolio |
|---|---|
| 4 abr | $104.86 |
| 15 may | **$120.33** (pico +15%) |
| 2 jun | $111.83 |
| 8 jun | $100.85 |
| 27 jun | $100.84 (congelado) |

De abril a mediados de mayo el bot hizo **exactamente lo que pides** —ganancias pequeñas y recurrentes— y subió +14.7%. La estrategia de micro-swings **es válida**; el daño llegó con el régimen bajista de junio.

### 7.3 El problema real: riesgo/beneficio negativo + sin filtro de régimen
**Distribución de las 116 ventas por señal (`signal_sell`):**
| Resultado | n | P&L medio |
|---|---|---|
| Pérdida (<0%) | 40 | −0.86% |
| +0–0.5% | 15 | +0.21% |
| +0.5–1% | 4 | +0.9% |
| +1–2% | 28 | +1.52% |
| +2–5% | 28 | +2.82% |
| >5% | 1 | +6.0% |

Las ganancias **ya son pequeñas y recurrentes** (mediana ~+1.5%). Pero mira los stops:

**Los 13 stop-losses (−$17.9 total) están TODOS concentrados en el crash de junio:**
| Fecha | Símbolo | P&L |
|---|---|---|
| 18 may | ETH | −5.1% |
| 22 may | BTC | −0.5% |
| 1 jun | BTC | −5.0% |
| 2 jun | BTC ×2 + ETH ×3 | −2.4% a −5.9% |
| 4 jun | BTC ×2 + ETH | −5.0% a −5.4% |
| **6 jun** | **ETH ×2** | **−13.0% y −10.9%** ← gap-downs |

**Asimetría letal:** ganas +1.5% promedio, pierdes −5% (y hasta −13% cuando el precio "gapea" a través del stop entre escaneos). Con esa relación, un 59% de aciertos **no alcanza** para ser rentable. 6 semanas de micro-ganancias borradas por 6 días de crash.

### 7.4 Números globales
| Métrica | Valor |
|---|---|
| Posiciones cerradas | 142 |
| Win rate | **59.2%** |
| P&L total | **−$3.74** (sobre ~$100 ≈ −3.7%) |
| P&L medio por posición | +0.3% |
| BTC neto | −$4.75 (25/50 wins) |
| ETH neto | +$1.00 (59/92 wins) |
| Hold medio (win) | 23.4 h |
| Hold medio (loss) | 23.9 h |

Observaciones de analista:
- **BTC es el lastre** (−$4.75) pese al gate de score≥6. ETH casi break-even.
- **Wins y losses duran lo mismo (~24h)** → la lógica de time-decay no corta perdedores antes; los mantiene tanto como a los ganadores. Un perdedor debería cortarse mucho antes.
- **Los −13% y −10.9% del 6-jun** demuestran que el `rapid_stop_check` (cada 2 min) y el stop ampliado en downtrend (×1.6) **no protegen contra gaps**: el stop teórico era ~−8% y la salida real fue −13%. En cripto, los stops de mercado en spot sufren slippage en caídas verticales.

### 7.5 Implicaciones — corrige las prioridades del §4
1. **El take-profit corto (§4-P0) es MENOS urgente de lo que estimé** — el bot ya vende en +1.5-2.8%. Útil para asegurar antes, pero no es la causa raíz.
2. **El filtro de régimen (§4-P1) es ahora PRIORIDAD #1.** Habría evitado los 13 stops de junio = todo el drawdown. Cambio de mayor impacto, de lejos.
3. **Reducir stop a −2.5% / mejorar R:R (§4-P2)** pasa de opcional a crítico: la asimetría +1.5%/−5% es lo que impide ser rentable.
4. **Nuevo — auto-reanudación tras circuit breaker.** Hoy un drawdown apaga el bot indefinidamente. Debe reanudar cuando (a) el drawdown se recupere bajo un umbral, o (b) tras N días con confirmación de régimen alcista. Sin esto, "ganancias recurrentes" es imposible: el bot pasa semanas apagado.
5. **Tamaño de cuenta (~$100):** con fee ~0.2% round-trip y mínimo $10/orden, el margen de scalping es estrecho. Confirmar fee tier en Bybit y no fragmentar $100 en 5 activos — concentrar en 1-2 con mejor liquidez.

### 7.6 Acción inmediata
- **No hagas `/resume` a ciegas.** El bot tiene config de "no-buy zone" y niveles de mediados de junio. Reanudar ahora = volver a comprar en mercado sin tendencia clara (BTC ~$62.7K, bajo EMA200).
- Primero implementar **filtro de régimen + R:R + auto-resume**, validar en backtest, y recién entonces reanudar.

---

## 8. Implementación v2.14 (rama `feat/regime-filter-rr`) + validación

Implementado en código (sin desplegar). Cambios:

| Componente | Antes | Ahora | Archivo |
|---|---|---|---|
| **Filtro de régimen** | inexistente | `strategy.regime_allows_buy()` — bloquea compra en downtrend confirmado salvo señal de giro | `strategy.py`, `bot.py` |
| **Take-profit** | inexistente (solo +8% al score) | `+3%` salida directa (`config.risk.take_profit_pct`) | `strategy.check_stop_loss`, `bot.py` |
| **Stop-loss** | −5% | **−3.5%** | `config.risk.stop_loss_pct` |
| **Trailing** | act +1.5% / dist 2% (incoherente) | act +1.5% / **dist 0.8%** | `config.risk` |
| **Auto-resume** | nunca (pausa indefinida) | reanuda si drawdown ≤ 6% | `bot._maybe_auto_resume` |
| **Cooldown post-stop** | 24h | **6h** | `config.risk` |
| **Pausa waterfall** | 7 días | **2 días** | `config.crash_detector` |
| EMA media (50, 4h) | — | nuevo indicador para el filtro | `strategy.compute_indicators` |

### Validación (backtest fiel que llama a las funciones reales; BTC+ETH, datos Bybit)
| Régimen | B&H | ACTUAL | PROPUESTA v2.14 |
|---|---|---|---|
| Bear 9m (sep25–jun26) | −48%/−65% | −12.3% (DD 14%) | **−7.8% (DD 10%)** |
| **Crash may–jun 26** | −25%/−32% | −7.5% (DD 9%) | **−1.1% (DD 2%)** |
| Recovery feb–may 26 | +16%/+11% | +4.7% | +1.3% |
| Bull mar–ago 25 | +10%/+66% | +4.8% (win 10%) | +3.2% (win **70%**) |

**Lectura:** la propuesta **no domina en todo** — cede algo de upside en mercados laterales-alcistas a cambio de **protección decisiva en crashes** (−7.5%→−1.1%, DD 9%→2%) y **15-20× menos trades** (menos fee drag). Dado que la cuenta se rompió en un crash, es el trade-off correcto. Largo-only spot sigue sin poder ganar en bear sostenido: el objetivo logrado es **preservar capital** y operar selectivamente con alto win-rate — el perfil real de "ganancias pequeñas y recurrentes".

### Pendiente antes de producción
1. **No desplegado.** Revisar el diff de la rama.
2. Decidir reanudación: con auto-resume, al desplegar habría que hacer `/resume` una vez (el drawdown actual ~16.7% > 6%, así que NO auto-reanudará hasta recuperar — considerar resetear el peak o `/resume` manual cuando el régimen sea favorable).
3. Confirmar fee tier Bybit (el TP +3% deja ~+2.8% neto a 0.1%/lado).
4. Opcional: validar también con `v 2.0/backtest.py` o ampliar a más activos.
