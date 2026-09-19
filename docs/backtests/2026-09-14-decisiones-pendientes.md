# Cierre de tres pendientes — 2026-09-14

## 1. `near_support_gate`: DESCARTADO

**Veredicto: dejar `near_support_gate_enabled = False` de forma permanente.**
No es "pendiente de backtest" — es una pregunta cerrada.

Tres razones, en orden de peso:

**Optimiza la métrica equivocada.** El gate bloquea salidas `signal_sell`.
Desde v2.14 (84 cierres, config actual), el P&L por bucket es:

| bucket | n | P&L |
|---|---|---|
| **stop_loss** | 5 | **−$2.43** ← único negativo |
| partial_sell | 6 | +$0.02 |
| force_sell | 1 | +$0.34 |
| partial_resistance | 4 | +$0.83 |
| **signal_sell** | 46 | **+$1.16** |
| trailing_stop | 6 | +$1.68 |
| **take_profit** | 15 | **+$8.16** ← mayor fuente |

`signal_sell` aporta +$0.025 por trade: prácticamente neutro. El gate lo
bloquearía esperando un rebote, convirtiendo salidas neutras en candidatas al
único bucket que pierde plata. El valor esperado es negativo.

Nota: esto **corrige** un dato anterior que decía que `signal_sell` era la mayor
fuente de ganancia (+$12.05 en 160 trades). Eso era cierto sobre el historial
completo, que mezcla la era del stop de 5%. Con la config actual, el que gana es
`take_profit`.

**El harness no puede validarlo.** `backtest.py:55` anula los niveles clave a
propósito, y el gate es 100% una feature de niveles clave. Documentado en
`2026-09-14-post-fidelity-fix.txt`.

**Shadow mode no cerraría la pregunta en tiempo útil.** Con 46 `signal_sell` en
~2.5 meses, y solo una fracción cerca de un soporte, juntar eventos suficientes
para distinguir señal de ruido llevaría meses.

**Qué se hace con el código:** se queda. Ya está escrito, testeado y compartido
vía `sell_rules.near_support_block`, con el flag apagado. Borrarlo sería churn
sin beneficio. Lo que cambia es que deja de ser una pregunta abierta.

## 2. `uptrend_reentry`: QUITARLO — pero no ahora

**Veredicto: desactivar `uptrend_reentry`, después de que cierre el canario.**

Corrido sobre `data_ago2026` (mar-2025 → ago-2026, BTC+ETH+BNB):

| config | return | cierres | win rate | PF | maxDD |
|---|---|---|---|---|---|
| COMBO (producción hoy) | 6.02% | 245 | 43.7% | 1.33 | 10.2% |
| **COMBO sin reentry** | **6.70%** | 240 | 43.8% | 1.35 | 10.4% |
| SELLFIX solo | 6.70% | 240 | 43.8% | 1.35 | 10.4% |

Dos lecturas:

**`uptrend_reentry` cuesta 0.68 puntos.** Agrega trades que churnean sin
compensar el costo de fees.

**`uptrend_guard_relax` es inerte, confirmado bit a bit.** "COMBO sin reentry" y
"SELLFIX solo" dan idénticos en las cinco métricas. El flag está cableado
(`strategy.py:171`) pero su precondición —RSI>70 **y** uptrend **y** precio NO
sobre la banda superior— casi nunca se cumple: cuando el RSI supera 70 en un
uptrend, el precio ya está extendido. Es rama muerta en la práctica.

**Por qué no se aplica hoy:** el canario de stops nativos (v2.20) arrancó hace
horas. Cambiar la estrategia en simultáneo haría imposible atribuir cualquier
anomalía de la próxima semana: ¿fue el stop nativo o fue el cambio de régimen?
Esa atribución es exactamente el motivo por el que el rollout es por canario.

Aplicar cuando el canario cierre: `uptrend_reentry = False` en `config.py`.
Impacto esperado: 6.02% → 6.70% sobre 18 meses.

**Límite honesto:** un solo período, con BTC cayendo 8.9%. El ranking podría
diferir en otro régimen. El mecanismo (reentry agrega churn) es intuitivo, pero
no está validado fuera de muestra.

## 3. Extender stops nativos a ETH y BTC: BLOQUEADO POR DISEÑO

No se puede cerrar hoy. El plan exige **una semana limpia** de canario en
BNBUSDT antes de sumar el segundo símbolo, y el canario todavía no disparó ni
una vez: no hubo compras de BNB desde el despliegue.

Forzarlo ahora anularía la única razón de hacer rollout escalonado.

### Actualización 2026-09-19 — el canario se movió a ETHUSDT

Cuatro días después seguía sin disparar: **cero posiciones de BNBUSDT entre el
2026-09-14 y el 2026-09-18**. El diagnóstico no es que el canario esté sano,
es que nunca se ejecutó — los stops nativos solo se colocan sobre una posición
viva. Eso además invalida el criterio de revisión: el punto 4 del checklist
lee "silencio en el log = sano", pero sin posición abierta el silencio está
garantizado y no distingue un reconciliador correcto de uno roto.

La causa es la elección del símbolo. BNB es el **8%** de las entradas (18 de
229 en 5.5 meses, una cada 4 días) contra **45%** de ETH. Un canario genera
evidencia a la velocidad a la que opera su símbolo, así que ponerlo sobre el
menos activo maximiza el tiempo hasta el veredicto — meses, en este caso.

Se movió a **ETHUSDT** (`config.native_stops.enabled_symbols`). Sigue siendo un
solo símbolo, así que la atribución se mantiene. El radio de daño sigue
acotado por `margin_pct = 1.5%`: con el proceso vivo dispara siempre el stop
del bot primero, y el nativo solo actúa si el bot está muerto — el escenario
que el canario existe para validar.

**El criterio de cierre no cambia**, y ahora sí se puede evaluar: una semana
limpia con posiciones ETH abiertas, verificando los 5 puntos del checklist de
`docs/superpowers/specs/2026-09-14-stops-nativos-bybit-design.md`. El punto 4
recién es informativo cuando hay una posición viva: mirar varios scans
seguidos sin compras ni ventas, con posición abierta. Silencio = sano;
actividad recurrente = revertir a `[]` y revisar `_misma` en `native_stops.py`.

Esto también corre la fecha de la decisión 2 (`uptrend_reentry`), que espera al
cierre de este canario.
