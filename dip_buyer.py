"""
dip_buyer.py — Detección de caídas intraday y compras de oportunidad.

Lógica:
  - Cada 15 min compara precio actual vs precio hace 4h
  - Si la caída supera DIP_TRIGGER_PCT (-4%) → ejecuta compra de DIP_BUY_MULTIPLIER × base
  - Respeta cooldown de DIP_COOLDOWN_HOURS entre dips del mismo activo
  - Máximo DIP_MAX_PER_DAY compras por activo por día (UTC)
  - Respeta circuit breaker (no actúa si el bot está pausado)
"""

import logging
from datetime import datetime, timezone, timedelta

import config
import database as db
import exchange
from exchange import ExchangeError
from risk_manager import BotPausedError, check_bot_paused

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Detección de caída
# ---------------------------------------------------------------------------

def _get_drop_pct(asset: str) -> float | None:
    """
    Calcula la variación de precio entre hace 4h y ahora.
    Retorna un número negativo si bajó (ej. -0.043 = -4.3%), positivo si subió.
    Retorna None si no hay datos suficientes.
    """
    try:
        candles = exchange.get_ohlcv(asset, interval=config.OHLCV_INTERVAL, limit=3)
        if len(candles) < 2:
            return None
        price_4h_ago = candles[-2].close   # cierre de la vela anterior (4h atrás)
        price_now    = candles[-1].close   # cierre de la vela actual
        return (price_now - price_4h_ago) / price_4h_ago
    except ExchangeError as exc:
        log.warning("%s — No se pudo obtener precio para dip check: %s", asset, exc)
        return None


# ---------------------------------------------------------------------------
# Validaciones de cooldown y límite diario
# ---------------------------------------------------------------------------

def _can_dip_buy(asset: str) -> tuple[bool, str]:
    """Retorna (True, '') si se puede ejecutar dip buy, o (False, motivo)."""

    # Límite diario
    buys_today = db.count_dip_buys_today(asset)
    if buys_today >= config.DIP_MAX_PER_DAY:
        return False, f"Límite diario alcanzado ({buys_today}/{config.DIP_MAX_PER_DAY})"

    # Cooldown
    last_ts = db.get_last_dip_buy_ts(asset)
    if last_ts is not None:
        last_ts_utc = last_ts.replace(tzinfo=timezone.utc) if last_ts.tzinfo is None else last_ts
        elapsed = datetime.now(timezone.utc) - last_ts_utc
        cooldown = timedelta(hours=config.DIP_COOLDOWN_HOURS)
        if elapsed < cooldown:
            remaining = cooldown - elapsed
            mins = int(remaining.total_seconds() / 60)
            return False, f"Cooldown activo ({mins} min restantes)"

    return True, ""


# ---------------------------------------------------------------------------
# Ejecución de dip buy
# ---------------------------------------------------------------------------

def _execute_dip_buy(asset: str, drop_pct: float, current_price: float) -> None:
    qty_usd = round(config.DCA_DAILY_USD[asset] * config.DIP_BUY_MULTIPLIER, 2)

    # Verificar balance suficiente
    try:
        usdt_available = exchange.get_spot_balance("USDT").available
    except ExchangeError as exc:
        log.error("Dip buy %s — No se pudo obtener balance: %s", asset, exc)
        return

    if usdt_available < qty_usd:
        log.warning("Dip buy %s — Balance insuficiente ($%.2f < $%.2f)", asset, usdt_available, qty_usd)
        qty_usd = round(usdt_available * 0.95, 2)
        if qty_usd < 1.0:
            log.warning("Dip buy %s — Monto ajustado demasiado bajo ($%.2f), cancelando", asset, qty_usd)
            return

    try:
        order = exchange.place_spot_market_buy(asset, qty_usd)
        qty_asset = round(qty_usd / current_price, 8) if current_price > 0 else None

        db.insert_trade(
            asset=asset,
            side="buy",
            order_type="market",
            qty_usd=qty_usd,
            qty_asset=qty_asset,
            price=current_price,
            score=None,
            multiplier=config.DIP_BUY_MULTIPLIER,
            order_id=order.order_id,
            note=f"dip_buy drop={drop_pct*100:.2f}%",
        )
        db.insert_dip_buy(
            asset=asset,
            qty_usd=qty_usd,
            price=current_price,
            drop_pct=drop_pct,
            order_id=order.order_id,
        )

        log.info(
            "Dip buy ejecutado: %s $%.2f @ $%.2f (caída=%.2f%%)",
            asset, qty_usd, current_price, drop_pct * 100,
        )

        # Notificación Telegram
        import notifications
        notifications.notify_dip_buy(asset, qty_usd, current_price, drop_pct)

    except ExchangeError as exc:
        log.error("Dip buy %s — Error ejecutando orden: %s", asset, exc)
        import notifications
        notifications.notify_api_error(f"dip_buy {asset}", str(exc))


# ---------------------------------------------------------------------------
# Punto de entrada — llamado por el scheduler cada 15 min
# ---------------------------------------------------------------------------

def run_dip_check(assets: list[str] = config.ASSETS) -> None:
    """Revisa todos los activos y ejecuta dip buys si corresponde."""
    try:
        check_bot_paused()
    except BotPausedError:
        return

    for asset in assets:
        drop_pct = _get_drop_pct(asset)
        if drop_pct is None:
            continue

        if drop_pct > -config.DIP_TRIGGER_PCT:
            # No hay caída suficiente — log solo si es notable
            if drop_pct < -0.02:
                log.debug("%s — Caída de %.2f%% (bajo umbral -%.0f%%)", asset, drop_pct * 100, config.DIP_TRIGGER_PCT * 100)
            continue

        log.info("%s — Caída detectada: %.2f%% (umbral: -%.0f%%)", asset, drop_pct * 100, config.DIP_TRIGGER_PCT * 100)

        ok, reason = _can_dip_buy(asset)
        if not ok:
            log.info("%s — Dip buy bloqueado: %s", asset, reason)
            continue

        # Obtener precio actual
        try:
            current_price = exchange.get_ticker(asset).last_price
        except ExchangeError as exc:
            log.error("%s — No se pudo obtener precio actual: %s", asset, exc)
            continue

        _execute_dip_buy(asset, drop_pct, current_price)
