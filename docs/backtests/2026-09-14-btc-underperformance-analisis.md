# BTC "underperformance": la premisa no se sostiene

Fecha: 2026-09-14 · Datos: producción (`root@$BOT_HOST`, 222 cierres) +
backtest `combo` sobre `data_ago2026` (mar-2025 → ago-2026, 245 cierres).

## La cifra que originó el pendiente

BTCUSDT: 64 cierres, **−$0.37**, 56.3% win rate. Se leyó como "BTC gana más de
lo que pierde pero igual pierde plata" → sospecha de defecto específico de BTC.

## Por qué la cifra engaña

`config.py:246` — `stop_loss_pct` era **5%** antes de v2.14 (27-jun-2026); hoy
es 3.5%. El agregado mezcla dos sistemas distintos. Cortando ahí:

| | trades | P&L | win rate |
|---|---|---|---|
| **Antes de v2.14** (stop 5%) | 142 | −$3.74 | — |
| — BTCUSDT | 50 | −$4.75 | 50.0% |
| — ETHUSDT | 92 | +$1.00 | 64.1% |
| **Desde v2.14** (stop 3.5%) | 80 | **+$9.28** | — |
| — BTCUSDT | 14 | **+$4.38** | **78.6%** |
| — ETHUSDT | 21 | +$3.82 | 85.7% |
| — BNBUSDT | 18 | +$1.54 | 66.7% |
| — XRPUSDT | 15 | +$0.38 | 53.3% |
| — SOLUSDT | 12 | −$0.84 | 58.3% |

Suma: −3.74 + 9.28 = **+5.54 USDT**, que coincide con el P&L total conocido.

**Con la configuración actual, BTC es el mejor activo del universo, no el peor.**
Aviso de muestra: n=14. No alcanza para afirmar que BTC *sea* el mejor; sí
alcanza para descartar que la cifra de −$0.37 indique un defecto de BTC.

Todos los stops anteriores a v2.14 caen entre −4.5% y −5.9% (el stop de 5%
funcionando) salvo los dos del blackout de junio (−13.0%, −10.9%). Todos los
posteriores caen entre −3.5% y −3.7%. El stop funciona como está diseñado.

## Segunda vía: el backtest, con 101 trades de BTC

Con la config actual sobre 18 meses:

| símbolo | n | P&L | win rate | buy & hold del período |
|---|---|---|---|---|
| BTCUSDT | 101 | −$0.28 | 34.7% | **−8.9%** |
| ETHUSDT | 66 | +$59.09 | 63.6% | +7.0% |
| BNBUSDT | 78 | +$60.56 | 38.5% | +15.8% |

**BTC cayó 8.9% en el período.** Una estrategia long-only que queda en cero
sobre un activo que bajó no está fallando. El P&L mensual lo confirma: +$35.39
en los 8 meses en que BTC subió, −$35.67 en los 7 en que bajó.

## Dos hipótesis que probé y descarté

**"Los umbrales fijos están mal calibrados para la menor volatilidad de BTC."**
El take-profit (+3% fijo) dispara en 9 de 101 salidas de BTC contra 22 de 66 en
ETH, lo que encajaba. Pero σ(4h) es BTC 0.91%, BNB 1.01%, ETH 1.39%: BNB casi
no es más volátil que BTC y sin embargo duplica la tasa de TP. La frecuencia de
TP sigue al **drift** del activo (+15.8%, +7.0%, −8.9%), no a su volatilidad.
No hay evidencia de mala calibración por volatilidad.

**"Las pérdidas se concentran en posiciones grandes → problema de sizing."**
Los dos peores meses de BTC (2025-10 y 2026-03) los dominan trades de $256 y
$412 contra un típico de $40-100. Pero por cuartil de tamaño, sobre los tres
símbolos:

| cuartil | n | tamaño medio | P&L total | win rate |
|---|---|---|---|---|
| Q1 | 61 | $18 | −$7.20 | 21.3% |
| Q2 | 61 | $48 | −$18.57 | 34.4% |
| Q3 | 61 | $97 | +$24.86 | 54.1% |
| Q4 | 62 | $294 | **+$120.27** | **64.5%** |

El sizing funciona: `calc_buy_amount` escala con el score, los scores altos
rinden mejor, y el cuartil más grande es el más rentable. Las dos pérdidas
grandes de BTC son varianza de cola, no un defecto.

## Conclusión

No hay defecto específico de BTC en evidencia. Queda una propiedad conocida —
la estrategia es long-only y direccional — que no es un bug.

## Residuo para otro día

En el backtest `signal_sell` es **negativo** en los tres símbolos (BTC −$15.92,
ETH +$3.60, BNB −$16.68), mientras que en producción fue la **mayor fuente de
ganancia** (+$12.05 en 160 trades). La explicación probable son los niveles
clave, que el harness anula (`backtest.py:55`) y de los que el `signal_sell` de
producción sí se beneficia. Es otra instancia del límite ya documentado.
