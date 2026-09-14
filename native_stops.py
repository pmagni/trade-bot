"""
Stops nativos en Bybit — núcleo puro.

Una orden condicional de venta por posición abierta, colocada POR DEBAJO del
stop del bot. Red de seguridad: con el bot vivo su propio stop dispara primero,
así que el comportamiento normal no cambia. Si el bot muere, la orden queda en
el exchange y protege igual.

Acá va el QUÉ colocar y el QUÉ cancelar. El CÓMO —mandar la orden, escribir en
la DB, notificar— vive en la cáscara. Entra estado, sale una decisión.

Importa solo `config` a propósito (ni `database` ni `pybit`), para poder
testearse en el venv mínimo — mismo criterio que `monitoring.py` y `sell_rules.py`.
"""

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger("native_stops")

LINK_PREFIX = "nsl-"


@dataclass(frozen=True)
class DesiredStop:
    """Una orden condicional que DEBERÍA existir para una posición abierta."""
    link_id: str
    symbol: str
    qty: float
    trigger_price: float


@dataclass(frozen=True)
class ReconcilePlan:
    """Qué hacer para que el exchange refleje el estado deseado."""
    to_place: List[DesiredStop]
    to_cancel: List[str]      # order_id del exchange, no link_id


def desired_stops(
    positions: List[dict],
    prices: Dict[str, float],
    margin: float,
    enabled: List[str],
    now_ms: int,
) -> Dict[int, DesiredStop]:
    """
    Estado deseado: qué órdenes condicionales deberían existir ahora.

    El trigger se deriva del `stop_loss` de cada posición, NO de la entrada,
    porque el stop del bot ya varía según el régimen (3.5% normal, 2.5% uptrend,
    5.6% downtrend). Un porcentaje fijo desde la entrada dejaría el nativo por
    encima del stop del bot en downtrend, y dispararía primero.

    Dos invariantes de seguridad. Si una posición no los cumple, NO se emite
    nada para ella: quedarse sin red es preferible a colocar una trampa que
    venda una posición sana.

    Retorna dict keyed by position_id (int). Cada DesiredStop lleva link_id
    con timestamp para garantizar unicidad en el exchange y evitar collapsar
    duplicados.
    """
    out = {}

    for pos in positions:
        symbol = pos["symbol"]
        if symbol not in enabled:
            continue

        stop_loss = pos.get("stop_loss") or 0
        if stop_loss <= 0:
            continue

        price = prices.get(symbol)
        if not price:
            logger.warning(f"{symbol}: sin precio, no se puede validar el stop nativo")
            continue

        trigger = stop_loss * (1 - margin)

        # Invariante 1: estrictamente por debajo del stop del bot.
        if trigger >= stop_loss:
            logger.warning(
                f"{symbol} pos#{pos['id']}: trigger {trigger} no queda bajo el "
                f"stop del bot {stop_loss} — sin stop nativo")
            continue

        # Invariante 2: por debajo del mercado. Si no, dispara al instante.
        if trigger >= price:
            logger.warning(
                f"{symbol} pos#{pos['id']}: trigger {trigger} sobre el mercado "
                f"{price} — sin stop nativo")
            continue

        pos_id = pos['id']
        link_id = f"{LINK_PREFIX}{pos_id}-{now_ms}"
        out[pos_id] = DesiredStop(
            link_id=link_id, symbol=symbol,
            qty=pos["qty"], trigger_price=trigger,
        )

    return out


def _misma(stop: DesiredStop, orden: dict) -> bool:
    """¿La orden existente ya es la deseada? Tolerancia relativa por floats."""
    try:
        qty = float(orden.get("qty") or 0)
        trigger = float(orden.get("triggerPrice") or 0)
    except (TypeError, ValueError):
        return False

    return (math.isclose(qty, stop.qty, rel_tol=1e-6)
            and math.isclose(trigger, stop.trigger_price, rel_tol=1e-6))


def parse_position_id(link_id: str) -> Optional[int]:
    """
    Extrae position_id de un link_id con formato 'nsl-{pos_id}-{now_ms}'.

    Pública (sin guion bajo) porque la cáscara también la consume, para
    resolver el position_id exacto a partir de un orderLinkId de una orden
    ya ejecutada — sin adivinar cuál posición se cerró.
    """
    if not link_id or not link_id.startswith(LINK_PREFIX):
        return None

    parts = link_id.split('-')
    # Esperamos: ['nsl', '{pos_id}', '{now_ms}']
    if len(parts) != 3:
        return None

    try:
        return int(parts[1])
    except (IndexError, ValueError):
        return None


def reconcile_plan(desired: Dict[int, DesiredStop],
                   actual: List[dict]) -> ReconcilePlan:
    """
    Converger: colocar lo que falta, cancelar lo que sobra, recolocar lo que
    cambió. Cancelar y recolocar en vez de `amend` a propósito: una operación
    menos que puede fallar a la mitad, y el resultado es idempotente.

    Matching by position_id: parsea position_id de cada orderLinkId actual.
    Solo se tocan órdenes cuyo orderLinkId empieza con LINK_PREFIX y tiene
    un position_id parseable. Una orden puesta a mano desde la app de Bybit
    no es asunto del bot.

    Si hay múltiples órdenes actuales para el mismo position_id, se mantiene
    la primera y se cancelan el resto como huérfanas.

    Un estado ya convergido produce un plan vacío. Eso es lo que hace seguro
    correr esto en cada scan, incluso si el now_ms cambió entre llamadas.
    """
    to_place = []
    to_cancel = []

    # Mapear position_id -> orden, detectando y cancelando duplicados
    nuestras = {}
    for orden in actual:
        link_id = orden.get("orderLinkId") or ""
        pos_id = parse_position_id(link_id)

        if pos_id is not None:
            if pos_id not in nuestras:
                nuestras[pos_id] = orden
            else:
                # Duplicado: mantener el primero, cancelar este
                to_cancel.append(orden["orderId"])

    # Comparar deseados contra actuales
    for pos_id, stop in desired.items():
        orden = nuestras.get(pos_id)
        if orden is None:
            to_place.append(stop)
        elif not _misma(stop, orden):
            to_cancel.append(orden["orderId"])
            to_place.append(stop)

    # Cancelar órdenes nuestras que no están en el estado deseado
    for pos_id, orden in nuestras.items():
        if pos_id not in desired:
            to_cancel.append(orden["orderId"])

    return ReconcilePlan(to_place=to_place, to_cancel=to_cancel)


def falta_balance(tracked_qty: float, balance: float, price: float,
                  dust_threshold: float) -> bool:
    """
    ¿Hay menos activo del que la DB dice que tenemos? Eso significa que una
    posición se cerró sin que el bot lo registre — típicamente un stop nativo
    que disparó con el proceso muerto.

    La comparación es en USDT, no en cantidad de base: `dust_threshold` está en
    USDT, y una misma cantidad significa cosas muy distintas en BTC y en BNB.

    El caso inverso (balance de más) es asunto de `_sweep_dust`, no de acá.
    """
    faltante = tracked_qty - balance
    if faltante <= 0:
        return False
    return faltante * price > dust_threshold
