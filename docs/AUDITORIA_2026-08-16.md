# Auditoría del Swing Bot — Universo de activos (v2.15 → v2.16)
**Fecha:** 2026-08-16 · **Versión auditada:** v2.15 · **Perspectiva:** analista quant / backtesting

---

## 0. Resumen ejecutivo

El backtest que validó el "modo uptrend" v2.15 (`docs/../trade-bot-v215-backtest-verdicts` en memoria, README v2.15) solo corrió sobre **BTC+ETH**. El bot en producción opera **5 activos** (`config.pairs.symbols`: BTC, ETH, SOL, XRP, BNB). Repetir el mismo backtest de 16.5 meses (mar-2025 → ago-2026, datos Bybit 4h/daily) sobre el universo real cambia el veredicto:

| Universo | Variante | Return | Win% | PF | Max DD | n cierres |
|---|---|---|---|---|---|---|
| BTC+ETH (validado en v2.15) | combo | +2.14% | 43.8% | 1.24 | 11.2% | 153 |
| **BTC+ETH+SOL+XRP+BNB (real, pre-cambio)** | combo | **−10.39%** | 49.4% | 0.85 | 12.5% | 160 |
| BTC+ETH+SOL+BNB (sin XRP) | combo | +0.26% | 51.1% | 1.13 | 12.1% | 139 |
| **BTC+ETH+BNB (v2.16, sin XRP/SOL)** | combo | **+7.17%** | 41.9% | 1.38 | 10.2% | 227 |
| BTC+ETH+BNB (v2.16) | baseline v2.14 | +7.20% | 45.8% | 1.40 | 10.4% | 238 |

**El "combo" que se declaró ganador en v2.15 en realidad pierde −10.4% sobre el universo real** — se validó en un subconjunto que no representa la cartera operada en vivo.

---

## 1. Atribución por símbolo (227-238 cierres, mismo periodo, universo de 5)

| Símbolo | Trades | P&L | Win% | Buy&Hold del periodo |
|---|---|---|---|---|
| BNBUSDT | 31 | +$50.7 | 45% | +8.6% |
| ETHUSDT | 27 | +$33.0 | 59% | +28.3% |
| BTCUSDT | 41 | +$9.4 | 49% | −18.4% |
| SOLUSDT | 33 | −$29.0 | 58% | −29.7% |
| **XRPUSDT** | 28 | **−$125.4** | **36%** | **−45.3%** |

XRP es, por lejos, el mayor lastre — casi el doble de la ganancia combinada de BTC+ETH+BNB. SOL también resta, aunque menos. Ambos vienen de tendencias estructurales bajistas de 6-16 meses (−30% a −45%), no de ruido de corto plazo: la primera mitad del periodo (bull generalizado) ya mostraba a SOL/XRP con el retorno más débil del grupo (+22%/+9% vs +107% ETH, +58% BNB), y la segunda mitad (bear generalizado, −30% a −50% en todo el grupo) no dejó margen para recuperarlo.

## 2. Por qué el filtro de régimen no protege a SOL/XRP

`regime_allows_buy()` (`strategy.py:299`) evalúa tendencia en 4h/EMA200. Su `reversal_override` (activo por defecto) permite comprar en downtrend confirmado ante un cruce MACD alcista o RSI saliendo de sobreventa. En una tendencia bajista de meses con rebotes muertos frecuentes — el patrón dominante de SOL/XRP este periodo — esto compra sistemáticamente rebotes que fallan.

Se probó desactivar `reversal_override` sobre el universo de 5: **empeora** (−12.88% vs −10.39%). El problema no es esa pieza — es que el bot trata SOL/XRP con las mismas reglas que BTC/ETH pese a tener beta mucho más asimétrico (menos upside en bull, más downside en bear). Ya existe el patrón para resolver esto de forma más fina si se quisiera reincorporarlos: `config.scoring.btc_min_buy_score` + `bot.py:80` exige score≥6 solo para BTC; no hay equivalente para SOL/XRP.

## 3. Cambio implementado (v2.16)

- `config.py` — `TradingPairs.symbols` / `base_assets`: fuera `SOLUSDT`/`XRPUSDT`. Universo: BTC, ETH, BNB.
- `config.py` — `key_levels`: removidos los bloques estáticos de SOL/XRP (ya desactualizados, Jun 2026).
- README: banner y cambio v2.16 documentado.
- Verificado: `config.py` importa limpio, `config.pairs.symbols == ['BTCUSDT','ETHUSDT','BNBUSDT']`, backtest reproduce +7.17%/+7.20% exacto sobre el config resultante.

No se tocó lógica de estrategia/riesgo — es un cambio de universo, de bajo riesgo de regresión.

## 4. Pendiente / alternativas no implementadas

- **Gate de score en vez de exclusión total** para SOL/XRP (p. ej. `sol_min_buy_score`/`xrp_min_buy_score` altos, replicando `btc_min_buy_score`) — permitiría reincorporarlos con exigencia mayor en vez de vetarlos. No implementado; requiere backtest dedicado.
- El "combo" v2.15 (guard_relax + reentry + sell_rise_off) no mejora sobre el universo BTC+ETH+BNB (+7.17% vs baseline +7.20%, prácticamente igual) — no hace daño, pero tampoco aporta aquí. Se deja activado por ahora (no hay evidencia para desactivarlo).
- No se pudo contrastar contra el historial real de producción (mismo bloqueo que en la auditoría de junio: acceso SSH al servidor no disponible en este entorno).

---

## 5. Antes de desplegar

Cuenta real (mainnet). Este cambio está en la rama `fix/trim-weak-universe-v2.16`, no en `main`. Antes de mergear/desplegar:

1. Revisar el diff.
2. **⚠️ Crítico — verificar posiciones abiertas antes de desplegar.** El scan loop principal (`bot.py:63`, `for symbol in config.pairs.symbols`) es lo único que evalúa stop-loss, take-profit, trailing y sell-score por símbolo. Si SOLUSDT/XRPUSDT salen de esa lista y el bot tiene posiciones abiertas en esos activos en producción, **el bot deja de gestionarlas por completo** — no solo bloquea compras nuevas: no revisa stop-loss ni vende, la posición queda huérfana y expuesta sin salida automática. No fue posible confirmar el estado real de producción desde este entorno (sin acceso SSH al servidor, igual que en la auditoría de junio — DB local vacía). Antes de desplegar: `ssh-add` + revisar `positions` en el servidor; si hay posiciones abiertas en SOL/XRP, cerrarlas manualmente (o vía `/force_sell`) **antes** de actualizar el código, o mantenerlas temporalmente en `pairs.symbols` hasta liquidarlas y recién entonces sacarlas.
