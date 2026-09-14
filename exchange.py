"""
Swing Trading Bot - Exchange Module
Wrapper for Bybit API (spot + futures).
Uses pybit SDK for authenticated requests.
Includes rate-limit throttling and retry logic.
"""

import logging
import time
import threading
from typing import Tuple
from pybit.unified_trading import HTTP
from config import config

logger = logging.getLogger("exchange")

# ─── RATE LIMIT CONSTANTS ───
MIN_REQUEST_INTERVAL = 0.25   # 250ms between API calls (max 4/sec)
MAX_RETRIES = 3               # Retry up to 3 times on rate limit
RETRY_BASE_DELAY = 2.0        # Exponential backoff base: 2s, 4s, 8s

# ─── v2.20: stops nativos ───
ORDER_HISTORY_LIMIT = 50      # límite de página de get_order_history


def _safe_float(v) -> float:
    """Parse float safely, returning 0.0 for None/empty/invalid."""
    try:
        return float(v) if v not in (None, "", "0", 0) else 0.0
    except (TypeError, ValueError):
        return 0.0


class Exchange:
    def __init__(self):
        self.client = HTTP(
            api_key=config.exchange.api_key,
            api_secret=config.exchange.api_secret,
            testnet=config.exchange.testnet,
        )
        self._min_order_cache = {}
        self._last_request_time = 0.0
        self._throttle_lock = threading.Lock()
        logger.info(f"Exchange initialized (testnet={config.exchange.testnet})")

    def _throttle(self):
        """Enforce minimum interval between API requests to avoid rate limits."""
        with self._throttle_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < MIN_REQUEST_INTERVAL:
                time.sleep(MIN_REQUEST_INTERVAL - elapsed)
            self._last_request_time = time.monotonic()

    def _call_with_retry(self, func, *args, **kwargs):
        """Call an API function with throttling and retry on rate-limit errors."""
        for attempt in range(MAX_RETRIES + 1):
            self._throttle()
            try:
                return func(*args, **kwargs)
            except Exception as e:
                err_str = str(e)
                is_rate_limit = "rate limit" in err_str.lower() or "ErrCode: 403" in err_str
                if is_rate_limit and attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        f"Rate limited (attempt {attempt+1}/{MAX_RETRIES}), "
                        f"retrying in {delay:.0f}s..."
                    )
                    time.sleep(delay)
                    continue
                raise

    # ─── MARKET DATA ───

    def get_price(self, symbol: str) -> float:
        """Get current market price."""
        try:
            resp = self._call_with_retry(self.client.get_tickers, category="spot", symbol=symbol)
            return float(resp["result"]["list"][0]["lastPrice"])
        except Exception as e:
            logger.error(f"Error getting price for {symbol}: {e}")
            raise

    def get_ohlcv(self, symbol: str, interval: str = "240", limit: int = 100) -> list:
        """
        Get OHLCV candles.
        interval: "1","3","5","15","30","60","120","240","360","720","D","W","M"
        Returns list of dicts with keys: timestamp, open, high, low, close, volume, turnover
        """
        try:
            resp = self._call_with_retry(
                self.client.get_kline,
                category="spot",
                symbol=symbol,
                interval=interval,
                limit=limit,
            )
            candles = resp["result"]["list"]
            # Bybit returns newest first, reverse to chronological
            candles.reverse()
            return [
                {
                    "timestamp": int(c[0]),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                    "turnover": float(c[6]),
                }
                for c in candles
            ]
        except Exception as e:
            logger.error(f"Error getting OHLCV for {symbol}: {e}")
            raise

    def get_orderbook(self, symbol: str, limit: int = 5) -> dict:
        """Get order book."""
        try:
            resp = self._call_with_retry(self.client.get_orderbook, category="spot", symbol=symbol, limit=limit)
            return {
                "bids": [(float(b[0]), float(b[1])) for b in resp["result"]["b"]],
                "asks": [(float(a[0]), float(a[1])) for a in resp["result"]["a"]],
            }
        except Exception as e:
            logger.error(f"Error getting orderbook for {symbol}: {e}")
            raise

    # ─── ACCOUNT DATA ───

    def get_balance(self, coin: str = "USDT") -> float:
        """Get available balance for a coin."""
        try:
            resp = self._call_with_retry(self.client.get_wallet_balance, accountType="UNIFIED", coin=coin)
            for account in resp["result"]["list"]:
                for c in account["coin"]:
                    if c["coin"] == coin:
                        total = _safe_float(c.get("walletBalance"))
                        avail = (
                            _safe_float(c.get("availableToWithdraw"))
                            or _safe_float(c.get("availableToBorrow"))
                            or _safe_float(c.get("equity"))
                            or total
                        )
                        return avail
            return 0.0
        except Exception as e:
            logger.error(f"Error getting balance for {coin}: {e}")
            raise

    def get_all_balances(self) -> dict:
        """Get all non-zero balances."""
        try:
            resp = self._call_with_retry(self.client.get_wallet_balance, accountType="UNIFIED")
            balances = {}
            for account in resp["result"]["list"]:
                for c in account["coin"]:
                    total = _safe_float(c.get("walletBalance"))
                    if total > 0:
                        avail = (
                            _safe_float(c.get("availableToWithdraw"))
                            or _safe_float(c.get("availableToBorrow"))
                            or _safe_float(c.get("equity"))
                            or total
                        )
                        balances[c["coin"]] = {
                            "total": total,
                            "available": avail,
                            "usd_value": _safe_float(c.get("usdValue")),
                        }
            return balances
        except Exception as e:
            logger.error(f"Error getting balances: {e}")
            raise

    # ─── SPOT TRADING ───

    def get_min_order_value(self, symbol: str) -> float:
        """Get minimum order value for a symbol."""
        if symbol in self._min_order_cache:
            return self._min_order_cache[symbol]
        try:
            resp = self._call_with_retry(self.client.get_instruments_info, category="spot", symbol=symbol)
            info = resp["result"]["list"][0]
            min_val = float(info.get("lotSizeFilter", {}).get("minOrderAmt", "10"))
            self._min_order_cache[symbol] = min_val
            return min_val
        except Exception:
            return 10.0  # Safe default

    def get_qty_precision(self, symbol: str) -> int:
        """Get quantity decimal precision for a symbol."""
        try:
            resp = self._call_with_retry(self.client.get_instruments_info, category="spot", symbol=symbol)
            info = resp["result"]["list"][0]
            base_precision = info.get("lotSizeFilter", {}).get("basePrecision", "0.000001")
            if "." in base_precision:
                return len(base_precision.split(".")[1].rstrip("0")) or 1
            return 0
        except Exception:
            return 6  # Safe default

    def place_spot_market_buy(self, symbol: str, quote_amount: float) -> dict:
        """
        Place a spot market buy order using quote currency (USDT).
        quote_amount: amount in USDT to spend.
        """
        min_val = self.get_min_order_value(symbol)
        if quote_amount < min_val:
            raise ValueError(
                f"Order value {quote_amount} USDT is below minimum {min_val} USDT for {symbol}"
            )

        try:
            resp = self._call_with_retry(
                self.client.place_order,
                category="spot",
                symbol=symbol,
                side="Buy",
                orderType="Market",
                qty=str(round(quote_amount, 2)),
                marketUnit="quoteCoin",
                timeInForce="IOC",
            )
            order_id = resp["result"]["orderId"]
            logger.info(f"BUY {symbol}: {quote_amount} USDT, order_id={order_id}")

            time.sleep(1)
            fill_info = self._get_order_fill(symbol, order_id)
            return {
                "order_id": order_id,
                "symbol": symbol,
                "side": "Buy",
                "qty": fill_info.get("qty", 0),
                "price": fill_info.get("price", 0),
                "value_usdt": fill_info.get("value", quote_amount),
                "fee": fill_info.get("fee", 0),
                "status": "filled",
            }
        except Exception as e:
            logger.error(f"Error placing buy for {symbol}: {e}")
            raise

    def place_spot_market_sell(self, symbol: str, qty: float) -> dict:
        """
        Place a spot market sell order.
        qty: amount of base asset to sell.
        """
        precision = self.get_qty_precision(symbol)

        # Clamp to real available balance to prevent error 170131.
        # Bybit deducts fees in the base asset on buys, so the wallet
        # balance is always slightly less than the qty we recorded.
        base_asset = symbol.replace("USDT", "")
        available = self.get_balance(base_asset)
        if available <= 0:
            raise ValueError(f"No {base_asset} balance available to sell")
        # Floor-truncate (never round up) so we never request more than we own
        factor = 10 ** precision
        actual_qty = int(min(qty, available) * factor) / factor
        if actual_qty <= 0:
            raise ValueError(f"Qty too small after truncation for {symbol}: {actual_qty}")

        qty_str = f"{actual_qty:.{precision}f}"

        try:
            resp = self._call_with_retry(
                self.client.place_order,
                category="spot",
                symbol=symbol,
                side="Sell",
                orderType="Market",
                qty=qty_str,
                timeInForce="IOC",
            )
            order_id = resp["result"]["orderId"]
            logger.info(f"SELL {symbol}: {qty_str}, order_id={order_id}")

            time.sleep(1)
            fill_info = self._get_order_fill(symbol, order_id)
            return {
                "order_id": order_id,
                "symbol": symbol,
                "side": "Sell",
                "qty": fill_info.get("qty", qty),
                "price": fill_info.get("price", 0),
                "value_usdt": fill_info.get("value", 0),
                "fee": fill_info.get("fee", 0),
                "status": "filled",
            }
        except Exception as e:
            logger.error(f"Error placing sell for {symbol}: {e}")
            raise

    def _get_order_fill(self, symbol: str, order_id: str) -> dict:
        """Get fill details for an order."""
        try:
            resp = self._call_with_retry(
                self.client.get_order_history,
                category="spot", symbol=symbol, orderId=order_id,
            )
            if resp["result"]["list"]:
                order = resp["result"]["list"][0]
                return {
                    "qty": float(order.get("cumExecQty", 0)),
                    "price": float(order.get("avgPrice", 0)),
                    "value": float(order.get("cumExecValue", 0)),
                    "fee": float(order.get("cumExecFee", 0)),
                }
        except Exception as e:
            logger.warning(f"Could not get fill info for {order_id}: {e}")
        return {}

    # ─── FUTURES (LEVERAGE) ───

    def set_leverage(self, symbol: str, leverage: float):
        """Set leverage for a futures symbol."""
        try:
            self._call_with_retry(
                self.client.set_leverage,
                category="linear",
                symbol=symbol,
                buyLeverage=str(int(leverage)),
                sellLeverage=str(int(leverage)),
            )
            logger.info(f"Set leverage for {symbol} to {leverage}x")
        except Exception as e:
            if "leverage not modified" not in str(e).lower():
                logger.error(f"Error setting leverage for {symbol}: {e}")
                raise

    def place_futures_market(self, symbol: str, side: str, qty: float,
                             leverage: float = 2.0) -> dict:
        """Place a futures market order with leverage."""
        self.set_leverage(symbol, leverage)

        try:
            resp = self._call_with_retry(
                self.client.place_order,
                category="linear",
                symbol=symbol,
                side=side,
                orderType="Market",
                qty=str(qty),
                timeInForce="IOC",
                positionIdx=0,
            )
            order_id = resp["result"]["orderId"]
            logger.info(f"FUTURES {side} {symbol}: {qty}, leverage={leverage}x, order_id={order_id}")
            return {"order_id": order_id, "status": "filled"}
        except Exception as e:
            logger.error(f"Error placing futures order for {symbol}: {e}")
            raise

    def close_futures_position(self, symbol: str) -> dict:
        """Close all futures positions for a symbol."""
        try:
            resp = self._call_with_retry(self.client.get_positions, category="linear", symbol=symbol)
            positions = resp["result"]["list"]
            results = []
            for pos in positions:
                size = float(pos.get("size", 0))
                if size > 0:
                    side = "Sell" if pos["side"] == "Buy" else "Buy"
                    result = self._call_with_retry(
                        self.client.place_order,
                        category="linear",
                        symbol=symbol,
                        side=side,
                        orderType="Market",
                        qty=str(size),
                        reduceOnly=True,
                        timeInForce="IOC",
                        positionIdx=0,
                    )
                    results.append(result)
            return {"closed": len(results)}
        except Exception as e:
            logger.error(f"Error closing futures position for {symbol}: {e}")
            raise

    # ─── v2.20: stops nativos ───

    def place_spot_stop_order(self, symbol: str, qty: float,
                              trigger_price: float, link_id: str) -> dict:
        """
        Orden condicional de venta spot (red de seguridad bajo el stop del bot).

        orderFilter="StopOrder" es obligatorio y NO es intercambiable con
        "tpslOrder": tpslOrder ocupa el activo base apenas se coloca, lo que
        dejaría al bot sin poder vender. StopOrder no lo ocupa hasta el trigger.
        """
        precision = self.get_qty_precision(symbol)
        factor = 10 ** precision
        actual_qty = int(qty * factor) / factor
        if actual_qty <= 0:
            raise ValueError(f"Qty too small after truncation for {symbol}: {qty}")
        qty_str = f"{actual_qty:.{precision}f}"

        resp = self._call_with_retry(
            self.client.place_order,
            category="spot",
            symbol=symbol,
            side="Sell",
            orderType="Market",
            qty=qty_str,
            orderFilter="StopOrder",
            triggerPrice=f"{trigger_price:.8f}".rstrip("0").rstrip("."),
            orderLinkId=link_id,
        )
        order_id = resp["result"]["orderId"]
        logger.info(
            f"STOP NATIVO {symbol}: {qty_str} @ trigger {trigger_price:.4f} "
            f"({link_id}) order_id={order_id}")
        return {"order_id": order_id}

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Cancela una orden por id. Usado para converger stops nativos."""
        resp = self._call_with_retry(
            self.client.cancel_order,
            category="spot",
            symbol=symbol,
            orderId=order_id,
            orderFilter="StopOrder",
        )
        logger.info(f"Cancelada orden {order_id} de {symbol}")
        return {"order_id": resp["result"]["orderId"]}

    def get_open_stop_orders(self, symbol: str) -> list:
        """Órdenes condicionales spot abiertas para el símbolo."""
        resp = self._call_with_retry(
            self.client.get_open_orders,
            category="spot",
            symbol=symbol,
            orderFilter="StopOrder",
        )
        return resp["result"]["list"]

    def get_filled_stop_orders(self, symbol: str, link_id_prefix: str = "nsl-",
                               lookback_hours: int = 168) -> list:
        """
        Órdenes condicionales nuestras que ya se ejecutaron.

        Sirve para reconstruir un cierre que ocurrió con el bot muerto: da el
        position_id (en el orderLinkId) y el precio de fill real.

        lookback_hours=168 (7 días) es el máximo que Bybit guarda de historial
        spot. El blackout de jun-2026 duró 55h, así que entra con margen.
        """
        start_ms = int((time.time() - lookback_hours * 3600) * 1000)
        resp = self._call_with_retry(
            self.client.get_order_history,
            category="spot",
            symbol=symbol,
            orderFilter="StopOrder",
            startTime=start_ms,
            limit=ORDER_HISTORY_LIMIT,
        )
        orders = resp["result"]["list"]
        if len(orders) >= ORDER_HISTORY_LIMIT:
            logger.warning(
                f"get_filled_stop_orders {symbol}: la página de historial "
                f"vino llena ({ORDER_HISTORY_LIMIT} en {lookback_hours}h), "
                f"el resultado puede estar incompleto")
        return [o for o in orders
                if (o.get("orderLinkId") or "").startswith(link_id_prefix)
                and o.get("orderStatus") == "Filled"]

    # ─── HEALTH CHECK ───

    def health_check(self) -> Tuple[bool, str]:
        """Check if exchange connection is healthy."""
        try:
            self._call_with_retry(self.client.get_server_time)
            self.get_balance("USDT")
            return True, "OK"
        except Exception as e:
            return False, str(e)


# Global instance
exchange = Exchange()
