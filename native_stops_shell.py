"""
Stops nativos — cáscara imperativa.

Separado de `native_stops.py` a propósito: ese módulo se mantiene puro (solo
config + stdlib) para poder testearse sin la stack del bot. Acá va el I/O.

Regla de oro: nada de lo que pase acá puede propagarse al loop de trading.
Un fallo de la API de Bybit no puede impedir que el bot opere.
"""

import logging
import time
from typing import List, Optional

import native_stops
from config import config
from database import db
from exchange import exchange
from notifications import notifier

logger = logging.getLogger("native_stops_shell")

# Estado de fallo por símbolo, en memoria. Sirve para avisar al entrar y al
# salir del estado, no en cada scan: una caída de Bybit generaría un mensaje
# cada 15 min. Tras un restart se vuelve a avisar una vez, que es lo deseado.
_en_fallo = {}


def _avisar(symbol: str, fallo: bool, detalle: str = ""):
    """
    Nunca puede lanzar: se llama, entre otros sitios, desde el `except` de
    `reconcile()` — justo cuando la red ya es sospechosa. `notifier.send_sync`
    solo atrapa `RuntimeError`; un fallo de Telegram de otro tipo (timeout,
    conexión) tiene que morir acá, no propagarse.

    El estado en `_en_fallo` se actualiza SIEMPRE, incluso si la notificación
    falló: si no, una alerta de "entrando en fallo" que no pudo mandarse deja
    el estado sin cambiar y el bot reintenta mandarla en cada scan siguiente,
    justo el spam que este throttle existe para evitar.
    """
    previo = _en_fallo.get(symbol, False)
    try:
        if fallo and not previo:
            notifier.send_sync(
                f"⚠️ <b>Stop nativo</b> {symbol}\nNo se pudo sincronizar: {detalle}")
        elif previo and not fallo:
            notifier.send_sync(f"✅ <b>Stop nativo</b> {symbol}\nSincronización restablecida")
    except Exception as e:
        logger.warning(f"Stops nativos {symbol}: no se pudo notificar ({type(e).__name__}: {e})")
    finally:
        _en_fallo[symbol] = fallo


def reconcile(symbols: Optional[List[str]] = None) -> None:
    """
    Converger las órdenes condicionales del exchange con las posiciones abiertas.

    Idempotente: si ya está convergido no hace nada. Por eso es seguro llamarlo
    en cada scan y también justo después de una compra.
    """
    habilitados = config.native_stops.enabled_symbols
    objetivo = [s for s in (symbols or habilitados) if s in habilitados]

    for symbol in objetivo:
        try:
            posiciones = [dict(p) for p in db.get_open_positions(symbol)]
            precios = {symbol: exchange.get_price(symbol)}

            deseadas = native_stops.desired_stops(
                posiciones, precios,
                margin=config.native_stops.margin_pct,
                enabled=habilitados,
                now_ms=int(time.time() * 1000),
            )
            actuales = exchange.get_open_stop_orders(symbol)
            plan = native_stops.reconcile_plan(deseadas, actuales)

            # Cancelar SIEMPRE antes de colocar: si una recolocación cancela y
            # coloca la misma posición, hacerlo al revés dejaría dos órdenes
            # vivas por un instante.
            for order_id in plan.to_cancel:
                exchange.cancel_order(symbol, order_id)
            for stop in plan.to_place:
                exchange.place_spot_stop_order(
                    stop.symbol, stop.qty, stop.trigger_price, stop.link_id)

            if plan.to_place or plan.to_cancel:
                logger.info(
                    f"Stops nativos {symbol}: {len(plan.to_place)} colocadas, "
                    f"{len(plan.to_cancel)} canceladas")

            _avisar(symbol, False)

        except Exception as e:
            logger.error(f"Stops nativos {symbol}: {type(e).__name__}: {e}")
            _avisar(symbol, True, f"{type(e).__name__}: {e}")
