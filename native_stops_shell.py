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


def detectar_cierres_externos(symbol: str) -> None:
    """
    Cerrar en la DB las posiciones que un stop nativo liquidó con el bot muerto.

    No se adivina cuál posición: se consulta el historial de órdenes filtrando
    por nuestro orderLinkId, lo que da el position_id exacto y el precio de fill
    real. Si no se encuentra el fill (blackout de más de 7 días, el máximo que
    Bybit guarda), se avisa para cerrar a mano en vez de inventar un precio.

    Este es el hueco que _sweep_dust no cubre: solo maneja el caso sobrante.
    """
    posiciones = [dict(p) for p in db.get_open_positions(symbol)]
    if not posiciones:
        return

    base_asset = symbol.replace("USDT", "")
    balance = exchange.get_balance(base_asset)
    tracked = sum(p["qty"] for p in posiciones)
    price = exchange.get_price(symbol)

    if not native_stops.falta_balance(
            tracked, balance, price, config.risk.dust_threshold_usdt):
        return

    logger.warning(
        f"{symbol}: balance {balance:.8f} < registrado {tracked:.8f} — "
        f"buscando cierres externos")

    llenadas = exchange.get_filled_stop_orders(symbol)
    por_id = {}
    for orden in llenadas:
        pos_id = native_stops.parse_position_id(orden.get("orderLinkId") or "")
        if pos_id is not None:
            por_id[pos_id] = orden

    abiertas = {p["id"]: p for p in posiciones}
    encontradas = 0
    sin_precio = []  # position_id de órdenes encontradas pero con fill_price inválido

    for pos_id, orden in por_id.items():
        pos = abiertas.get(pos_id)
        if pos is None:
            continue

        fill_price = float(orden.get("avgPrice") or 0)
        if fill_price <= 0:
            # No se inventa un precio: la posición queda abierta, pero hay
            # que avisar — si no, nadie se entera de que quedó sin cerrar.
            sin_precio.append((pos_id, orden.get("orderId") or "?"))
            continue

        recorded_qty = pos["qty"]
        executed_qty = float(orden.get("cumExecQty") or 0)
        qty = executed_qty or recorded_qty

        if native_stops.fill_incompleto(recorded_qty, executed_qty):
            # Ruling: no reabrir el remanente como posición nueva acá — ese
            # path de escritura a la DB no se puede testear sin mock del
            # exchange, y el riesgo de una posición fantasma o doble conteo
            # es peor que el problema (el remanente sigue en la wallet y
            # _sweep_dust lo recoge). Lo que no puede ser es silencioso.
            logger.error(
                f"{symbol}: posición #{pos_id} — fill parcial: ejecutado "
                f"{executed_qty:.8f} de {recorded_qty:.8f} registrado, "
                f"precio ${fill_price:,.2f}. Se cierra la posición completa "
                f"pero el P&L solo cubre lo ejecutado — reconciliar a mano.")

        pnl_usdt = (fill_price - pos["entry_price"]) * qty
        pnl_pct = (fill_price - pos["entry_price"]) / pos["entry_price"]
        db.close_position(pos_id, fill_price, pnl_usdt, pnl_pct, "native_stop")
        encontradas += 1

        if native_stops.fill_incompleto(recorded_qty, executed_qty):
            # Se avisa DESPUÉS de cerrar la posición: `send_sync` solo atrapa
            # RuntimeError, así que una caída de Telegram (timeout, conexión)
            # propagaría y dejaría la posición sin cerrar — abortando en el
            # mismo punto en cada scan siguiente. La posición ya convergió en
            # la DB pase lo que pase con la notificación.
            notifier.send_sync(
                f"⚠️ <b>Fill parcial en stop nativo</b> {symbol}\n"
                f"Posición #{pos_id}: ejecutado {executed_qty:.8f} de "
                f"{recorded_qty:.8f} registrado, a ${fill_price:,.2f}\n"
                f"<i>Se cierra la posición pero el P&L registrado solo cubre "
                f"la parte ejecutada. El remanente queda en la wallet y lo "
                f"recoge _sweep_dust — reconciliar el P&L a mano.</i>")

        notifier.send_sync(
            f"🛡 <b>Stop nativo ejecutado</b> {symbol}\n"
            f"Posición #{pos_id} cerrada a ${fill_price:,.2f}\n"
            f"P&L: ${pnl_usdt:+.2f} ({pnl_pct:+.2%})\n"
            f"<i>Se ejecutó en el exchange, probablemente con el bot caído.</i>")
        logger.info(
            f"{symbol}: posición #{pos_id} cerrada por stop nativo a {fill_price}")

    if sin_precio:
        detalle = ", ".join(f"#{pos_id} (orden {order_id})" for pos_id, order_id in sin_precio)
        notifier.send_sync(
            f"⚠️ <b>Orden sin precio de fill</b> {symbol}\n"
            f"Se encontró la orden que cerró estas posiciones pero sin "
            f"avgPrice válido: {detalle}\n"
            f"<i>Quedan abiertas — no se inventa un precio. Revisar a mano.</i>")
        logger.error(
            f"{symbol}: órdenes encontradas sin fill_price válido, "
            f"posiciones sin cerrar: {detalle}")

    if encontradas == 0 and not sin_precio:
        notifier.send_sync(
            f"⚠️ <b>Descuadre de balance</b> {symbol}\n"
            f"Registrado {tracked:.8f}, disponible {balance:.8f}, y no se "
            f"encontró la orden que lo explique.\n"
            f"<i>Revisar a mano — no se inventa un precio de cierre.</i>")
        logger.error(
            f"{symbol}: descuadre sin orden que lo explique "
            f"(tracked={tracked}, balance={balance})")


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
            detectar_cierres_externos(symbol)

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
