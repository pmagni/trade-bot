"""
Núcleo puro de la decisión de venta.

Este módulo existe por una razón concreta: `bot.py` y `backtest.py` tenían cada
uno su propia implementación de la misma decisión, y divergieron. El harness
validaba un sistema distinto al que corría en producción (ver el registro de
baselines en `docs/backtests/`).

Regla: acá va el QUÉ vender y POR QUÉ. El CÓMO ejecutarlo — mandar la orden,
escribir en la DB, notificar — queda en cada caller. Entra estado, sale una
decisión; sin I/O, sin efectos.

Importa solo `config` a propósito (ni `database` ni `numpy`), para que pueda
testearse en el venv mínimo — mismo criterio que `monitoring.py`.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from config import config


@dataclass(frozen=True)
class SellPlan:
    """
    Qué vender de UNA posición.

    runner_qty > 0 significa: cerrar `qty` y dejar el resto abierto con trailing
    en `runner_trailing`. El caller debe confirmar con `runner_is_viable()` al
    precio de fill real antes de sostener el runner.
    """
    qty: float
    reason: str
    runner_qty: float
    runner_trailing: float


def calc_sell_qty(sell_score: int, position_qty: float) -> float:
    """Cuánto vender según el tramo de score. Antes vivía en strategy.py."""
    SC = config.scoring

    if sell_score >= SC.sell_total:
        pct = SC.sell_total_pct
    elif sell_score >= SC.sell_strong:
        pct = SC.sell_strong_pct
    elif sell_score >= SC.sell_moderate:
        pct = SC.sell_moderate_pct
    elif sell_score >= SC.sell_partial:
        pct = SC.sell_partial_pct
    else:
        return 0

    return position_qty * pct


def runner_is_viable(runner_qty: float, price: float) -> bool:
    """Un runner por debajo del mínimo de orden no puede sostenerse."""
    return runner_qty > 0 and runner_qty * price >= config.risk.min_order_usdt


def plan_sell(
    sell_score: int,
    sell_details: dict,
    position_qty: float,
    price: float,
) -> Optional[SellPlan]:
    """
    Decide qué vender de una posición. None = no vender nada.

    Tres reglas, en orden:
      1. Sizing por tramo de score.
      2. Venta parcial en resistencia: con señal no-fuerte sobre una resistencia
         clave, vender `partial_sell_pct` y dejar el resto corriendo con trailing.
      3. Piso de orden: si el parcial no llega a `min_order_usdt`, cerrar todo
         en vez de mandar una orden que el exchange rechazaría.
    """
    SC = config.scoring

    qty = calc_sell_qty(sell_score, position_qty)
    if qty <= 0:
        return None

    at_resistance = "key_resistance" in sell_details
    if at_resistance and SC.partial_sell_at_resistance and sell_score < SC.sell_strong:
        qty = position_qty * SC.partial_sell_pct
        runner_qty = position_qty - qty
        runner_trailing = price * (1 - config.risk.trailing_stop_distance)
        reason = "partial_resistance"
    else:
        runner_qty = 0.0
        runner_trailing = 0.0
        reason = "signal_sell"

    if qty * price < config.risk.min_order_usdt:
        qty = position_qty
        runner_qty = 0.0
        runner_trailing = 0.0
        reason = "signal_sell"

    return SellPlan(qty=qty, reason=reason, runner_qty=runner_qty,
                    runner_trailing=runner_trailing)


def near_support_block(price: float, supports: List[dict]) -> Tuple[bool, Optional[str]]:
    """
    Compuerta de soporte cercano (v2.17): espejo del check de resistencia en
    compra. No vender justo sobre un soporte clave.

    OFF por defecto — ver `SellGuardConfig`. Con el flag apagado nunca bloquea.
    """
    if not config.sell_guard.near_support_gate_enabled:
        return False, None

    for s in supports or []:
        dist = abs(price - s["price"]) / s["price"]
        if dist <= config.key_levels.tolerance_pct:
            return True, f"near support {s['label']} ${s['price']:,.0f}"

    return False, None
