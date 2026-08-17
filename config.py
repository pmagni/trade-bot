"""
Swing Trading Bot - Configuration
All configurable parameters in one place.
Load sensitive values from .env file.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ExchangeConfig:
    """Bybit API configuration."""
    api_key: str = os.getenv("BYBIT_API_KEY", "")
    api_secret: str = os.getenv("BYBIT_API_SECRET", "")
    testnet: bool = os.getenv("BYBIT_TESTNET", "false").lower() == "true"


@dataclass
class TelegramConfig:
    """Telegram bot configuration."""
    token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")


@dataclass
class TradingPairs:
    """Trading pair configuration.

    v2.16 — SOLUSDT y XRPUSDT removidos del universo. Backtest 16.5m (mar-2025→
    ago-2026, datos Bybit) sobre el universo completo de 5 activos: combo v2.15
    -10.39% (XRP solo: -$125.4 de P&L, win 36%; SOL: -$29.0, win 58% — ambos en
    downtrend estructural -30% a -45% del periodo). Sin XRP/SOL: BTC+ETH+BNB
    combo +7.17% / baseline +7.20%, PF 1.38-1.40, 227 cierres. Ver
    docs/AUDITORIA_2026-08-16.md antes de reincorporarlos sin re-backtestear.
    """
    symbols: list = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "BNBUSDT",
    ])
    base_assets: list = field(default_factory=lambda: [
        "BTC", "ETH", "BNB",
    ])
    quote_asset: str = "USDT"


@dataclass
class ScanningConfig:
    """Market scanning parameters."""
    interval_minutes: int = 15          # How often to scan (base rate)
    fast_interval_minutes: int = 5       # Scan every 5 min during high volatility
    ohlcv_timeframe: str = "240"        # 4h candles (Bybit format: minutes)
    ohlcv_limit: int = 100              # Number of candles to fetch
    daily_timeframe: str = "D"          # Daily candles for trend
    daily_limit: int = 90               # 90 days of daily data


@dataclass
class IndicatorConfig:
    """Technical indicator parameters."""
    # RSI
    rsi_period: int = 14
    rsi_oversold: float = 35.0
    rsi_extreme_oversold: float = 25.0
    rsi_overbought: float = 70.0
    rsi_extreme_overbought: float = 80.0

    # Bollinger Bands
    bb_period: int = 20
    bb_std: float = 2.0
    bb_low_threshold: float = 0.05      # %B below this = buy signal
    bb_high_threshold: float = 0.95     # %B above this = sell signal

    # EMA
    ema_fast: int = 9
    ema_slow: int = 21
    ema_trend: int = 200                # Long-term trend

    # MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # Volume
    volume_period: int = 20
    volume_buy_multiplier: float = 2.0   # Vol > 2x avg = buy signal
    volume_sell_multiplier: float = 2.5  # Vol > 2.5x avg = sell climax

    # Fear & Greed
    fg_extreme_fear: int = 25
    fg_extreme_greed: int = 75

    # Price drop/rise from local high/low
    local_period_days: int = 14
    drop_moderate: float = 0.05          # 5% drop
    drop_strong: float = 0.10            # 10% drop
    rise_moderate: float = 0.08          # 8% rise
    rise_strong: float = 0.15            # 15% rise

    # Fibonacci levels
    fib_levels: list = field(default_factory=lambda: [0.236, 0.382, 0.5, 0.618, 0.786])

    # Pivot Points
    pivot_zone_tolerance: float = 0.01   # 1% tolerance around pivot levels


@dataclass
class ScoringConfig:
    """Score thresholds for buy/sell actions."""
    # Buy score thresholds
    buy_no_action: int = 4              # Score 0-4: don't buy
    buy_light: int = 5                  # Score 5: buy 8%
    buy_moderate: int = 6               # Score 6: buy 28%
    buy_strong: int = 7                 # Score 7-9: buy 48%
    buy_maximum: int = 10               # Score 10+: buy 60%

    # Buy capital allocation — opportunity-sized (v2.9)
    buy_light_pct: float = 0.08          # Score 5: probe (was 0.10)
    buy_moderate_pct: float = 0.28       # Score 6: confirmed setup (was 0.30)
    buy_strong_pct: float = 0.48         # Score 7-9: strong signal (was 0.45)
    buy_maximum_pct: float = 0.60        # Score 10+: max conviction (was 0.60, unchanged)

    # DCA escalation (v2.9)
    dca_multiplier_2nd: float = 1.5      # 2nd position: 1.5× base size
    dca_multiplier_3rd: float = 2.0      # 3rd+ position: 2.0× base size
    dca_score_min_2nd: int = 6           # Min score to open 2nd position in same asset
    dca_score_min_3rd: int = 7           # Min score to open 3rd+ position in same asset
    dca_max_per_trade_pct: float = 0.50  # Hard cap: no single trade > 50% of available

    # Symbol-specific minimum buy scores (v2.10)
    # BTC score-5 trades: 55% win rate, +0.3% avg — below acceptable threshold
    # BTC score-6 trades: 100% win rate, +3.5% avg — require confirmed signal
    btc_min_buy_score: int = 6           # BTC needs stronger confirmation than ETH

    # Sell score thresholds (raised: score-4 sells had 43% win rate — too early)
    sell_no_action: int = 4
    sell_partial: int = 5               # Score 5-6: sell 25%
    sell_moderate: int = 6              # Score 6-7: sell 50%
    sell_strong: int = 8               # Score 8-9: sell 75%
    sell_total: int = 10               # Score 10+: sell 100%

    # Sell position allocation (% of position)
    sell_partial_pct: float = 0.25
    sell_moderate_pct: float = 0.50
    sell_strong_pct: float = 0.75
    sell_total_pct: float = 1.00

    # Profit target for sell score bonus
    profit_target_pct: float = 0.08     # 8% profit = +2 sell score

    # Partial sell: sell portion at resistance, let rest ride with trailing
    partial_sell_at_resistance: bool = True
    partial_sell_pct: float = 0.50      # Sell 50% at resistance, keep 50% as runner


@dataclass
class CrashDetectorConfig:
    """
    Crash detector — suspende compras automáticamente durante caídas aceleradas.

    Lógica (v2.12):
      - Si el precio cayó > crash_threshold_24h en las últimas 24h  → pausa por símbolo
      - Si hay ≥ consecutive_sl_limit stop-losses en los últimos consecutive_sl_days días
        para el mismo activo → pausa extendida por símbolo (waterfall protection)
      - Ambas pausas usan el sistema de cooldowns de buy por símbolo.
    """
    enabled: bool = True

    # Velocidad de caída: congela compras si el activo cayó más de X% en 24h
    crash_threshold_24h: float = 0.10     # -10% en 24h → freeze buy por símbolo 48h
    crash_pause_hours: int = 48           # Duración del freeze por crash

    # Stop-losses consecutivos: waterfall protection
    consecutive_sl_limit: int = 2         # ≥2 stop-losses en N días → pausa larga
    consecutive_sl_days: int = 5          # Ventana de tiempo para contarlos
    consecutive_sl_pause_hours: int = 48  # v2.14: 2 días (era 7). El filtro de régimen ya
                                          # evita la mayoría de cascadas; 7d apagaba el bot de más.

    # Stop-loss más ancho cuando el precio está bajo EMA200 (downtrend)
    # Evita "gapping" — ser forzado a salir muy por debajo del stop calculado
    # entre scans de 5 min. En uptrend: stop = 5%. En downtrend: 5% × 1.6 = 8%
    downtrend_stop_multiplier: float = 1.6   # multiplicador del stop_loss_pct en bear market


@dataclass
class RegimeConfig:
    """
    v2.14 — Filtro de régimen. La pieza de mayor impacto según la auditoría jun-2026.

    Backtest (16 meses, BTC+ETH): activar este filtro cortó las pérdidas en bear
    market ~30-78% sin estrangular las ganancias en bull (win-rate 19%→76%).

    Lógica: NO comprar reversión cuando el activo está en downtrend confirmado
    (precio < EMA50 4h con pendiente bajista Y bajo EMA200), SALVO que haya una
    señal genuina de giro (cruce MACD alcista o RSI saliendo de sobreventa).
    Esto evita "atrapar cuchillos" en caídas verticales como la de junio 2026.
    """
    enabled: bool = True
    ema_mid_period: int = 50            # EMA media (sobre velas 4h) que define el downtrend de corto plazo
    reversal_override: bool = True      # permitir compra en downtrend si hay señal de giro confirmada

    # v2.15 — Modo uptrend (uptrend = precio > EMA200 y EMA50 4h ascendente).
    # Defaults calibrados por backtest 16 meses BTC+ETH (backtest.py, jul-2026):
    #   baseline v2.14: -3.45% | combo (guard+reentry+sellfix): -1.38%, PF 1.09.
    # Las piezas descartadas EMPEORAN el resultado — no activarlas sin re-backtestear:
    #   pullback_score: -8.8% (compra pullbacks que siguen cayendo; los dips a banda
    #                   inferior de BB ya los captura el scoring normal)
    #   tight_stop:     resta ~0.8pp dentro del combo (2.5% es poco para vol 4h)
    uptrend_mode_enabled: bool = True
    uptrend_guard_relax: bool = True      # guard: RSI>70 solo bloquea con precio > banda sup.
    uptrend_pullback_score: bool = False  # DESCARTADO por backtest (ver arriba)
    uptrend_reentry: bool = True          # añadir a posiciones en ganancia sin spread -2%
    uptrend_tight_stop: bool = False      # DESCARTADO por backtest (ver arriba)
    uptrend_sell_rise_off: bool = True    # no puntuar rise_from_low como señal de venta
    uptrend_pullback_min: float = 0.015   # retroceso mínimo desde máximo 48h para puntuar
    uptrend_pullback_max: float = 0.035   # más allá de esto ya no es pullback sano
    uptrend_rsi_pullback_lo: float = 40.0 # RSI en zona de pullback sano...
    uptrend_rsi_pullback_hi: float = 55.0 # ...viniendo de >60 reciente
    uptrend_stop_loss_pct: float = 0.025  # stop en entradas de uptrend (era 3.5% general)


@dataclass
class RiskConfig:
    """Risk management parameters."""
    # Stop-loss
    stop_loss_pct: float = 0.035        # v2.14: -3.5% (era -5%); ×1.6 en downtrend (v2.12)
    take_profit_pct: float = 0.03       # v2.14: toma de ganancia a +3% (0 = desactivado).
                                        # Asegura el perfil "ganancias pequeñas recurrentes".
    # v2.15 — TP parcial con runner: al tocar +3% vende take_profit_sell_pct y deja
    # correr el resto con trailing. DESACTIVADO por backtest 16m: -7.7% vs -3.45%
    # baseline — el runner devuelve la ganancia porque el trailing solo se honra
    # sobre la activación (+1.5%) y en caídas rápidas el runner cae hasta el stop.
    take_profit_partial: bool = False
    take_profit_sell_pct: float = 0.50  # fracción vendida al tocar el take-profit
    trailing_stop_activation: float = 0.015  # Activate trailing at +1.5%
    trailing_stop_distance: float = 0.008   # v2.14: 0.8% (< activación → realmente asegura la ganancia)

    # Position sizing
    max_per_trade_pct: float = 0.40     # Max 40% of available per trade
    min_reserve_pct: float = 0.10       # Always keep 10% in USDT
    min_order_usdt: float = 10.0        # Bybit minimum
    max_open_positions_per_asset: int = 4   # was 10 — cap DCA at 4 levels
    min_entry_spread_pct: float = 0.02      # New entry must be ≥2% below cheapest open entry

    # Portfolio limits
    max_daily_loss_pct: float = 0.05    # -5% daily circuit breaker
    max_drawdown_pct: float = 0.12      # -12% total circuit breaker (era 15% — activaba tarde)

    # v2.14 — Auto-reanudación tras circuit breaker.
    # ANTES: un drawdown pausaba el bot indefinidamente (quedó 12 días apagado en jun-2026).
    # AHORA: reanuda solo cuando el drawdown se recupera por debajo del umbral.
    auto_resume_enabled: bool = True
    auto_resume_drawdown_pct: float = 0.06   # reanuda cuando drawdown actual <= 6%

    # Leverage (optional, disabled by default)
    leverage_enabled: bool = False
    leverage_max: float = 2.0
    leverage_score_threshold: int = 10  # Only at score 10+
    leverage_max_portfolio_pct: float = 0.15
    leverage_stop_loss_pct: float = 0.04
    leverage_max_duration_days: int = 5

    # Dust management: sell leftover crypto < this USD value
    dust_threshold_usdt: float = 5.0    # Sweep dust worth > $5

    # Cooldowns (in minutes)
    cooldown_after_buy: int = 30        # 30 minutes
    cooldown_after_sell: int = 30       # 30 minutes
    cooldown_after_stop_loss: int = 360   # v2.14: 6h (era 24h). Con stop -3.5% las pérdidas
                                          # son pequeñas; 24h apagaba el bot tras cada ruido.
    min_hold_minutes: int = 60          # Don't sell within 60 min of buying
    max_trades_per_day_per_asset: int = 2


@dataclass
class KeyLevelsConfig:
    """Manual key support/resistance levels per asset (multi-tier).
    Each asset has lists of supports/resistances with price, label, and score_bonus.
    Optional stop_level overrides percentage-based stop-loss.

    IMPORTANTE: actualizar al cambiar de régimen. El bot loguea WARNING si > 7 días sin cambio.
    updated_at se usa solo para tracking — no tiene efecto en la lógica.
    """
    updated_at: str = "2026-06-15"  # Actualizar al modificar los niveles

    levels: dict = field(default_factory=lambda: {
        "BTCUSDT": {
            # Updated Jun 15 — BTC en ~$66,700 (rally desde $60.5K; RSI 78 overbought)
            # Rally activo: esperar pullback a $63-64K para comprar.
            # stop_level: $60K = aprox -8% desde zona de entrada $63-65K.
            "supports": [
                {"price": 63000, "label": "Zona de consolidacion / primer soporte", "score_bonus": 1},
                {"price": 60000, "label": "Soporte psicologico fuerte",             "score_bonus": 2},
                {"price": 57000, "label": "Zona de acumulacion previa",             "score_bonus": 3},
                {"price": 53000, "label": "Soporte estructural profundo",           "score_bonus": 3},
            ],
            "resistances": [
                {"price": 67000, "label": "Resistencia inmediata (zona actual)",    "score_bonus": 1},
                {"price": 69000, "label": "Resistencia clave 2024",                "score_bonus": 2},
                {"price": 73000, "label": "Resistencia historica pre-ATH",         "score_bonus": 2},
            ],
            "stop_level": 59000,
        },
        "ETHUSDT": {
            # Updated Jun 15 — ETH en ~$1,816 (rally desde $1,550; RSI 84 extreme overbought)
            # Rompió $1,780 resistencia → ahora soporte. Esperar pullback a $1,700-1,750.
            # stop_level: $1,580 = aprox -8.5% desde zona de entrada $1,700-1,750.
            "supports": [
                {"price": 1780, "label": "Resistencia rota → nuevo soporte",       "score_bonus": 1},
                {"price": 1700, "label": "Zona de consolidacion / pullback ideal",  "score_bonus": 2},
                {"price": 1600, "label": "Soporte fuerte / previo piso",            "score_bonus": 3},
                {"price": 1450, "label": "Soporte estructural profundo",            "score_bonus": 3},
            ],
            "resistances": [
                {"price": 1830, "label": "Resistencia inmediata (zona actual)",    "score_bonus": 1},
                {"price": 1900, "label": "Resistencia mayor",                      "score_bonus": 2},
                {"price": 2000, "label": "Resistencia psicologica clave",          "score_bonus": 2},
            ],
            "stop_level": 1580,
        },
        "BNBUSDT": {
            # Added Jun 13 — BNB en ~$609 (exchange token con utilidad propia)
            # Rendimiento estable +6% 7d; menos volatil que otros altcoins.
            # stop_level: $555 = aprox -9% desde zona de entrada $580-610.
            "supports": [
                {"price": 590, "label": "Soporte de corto plazo",             "score_bonus": 1},
                {"price": 560, "label": "Zona de acumulacion reciente",       "score_bonus": 2},
                {"price": 520, "label": "Soporte estructural 2024",           "score_bonus": 2},
                {"price": 480, "label": "Zona de acumulacion profunda",       "score_bonus": 3},
            ],
            "resistances": [
                {"price": 625, "label": "Resistencia inmediata",              "score_bonus": 1},
                {"price": 650, "label": "Zona de resistencia media",          "score_bonus": 2},
                {"price": 700, "label": "Resistencia historica / ATH zone",   "score_bonus": 2},
            ],
            "stop_level": 555,
        },
    })
    tolerance_pct: float = 0.008  # 0.8% proximity threshold (was 0.5%)


@dataclass
class ReportingConfig:
    """Reporting and notification settings."""
    scanning_report_interval_hours: int = 6  # Report market status every 6h
    daily_report_hour_utc: int = 23          # ~20:00 CLT
    weekly_report_day: int = 6               # Sunday (0=Monday)
    weekly_report_hour_utc: int = 23


@dataclass
class DatabaseConfig:
    """Database configuration."""
    db_path: str = "swing_bot.db"


@dataclass
class Config:
    """Master configuration."""
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    pairs: TradingPairs = field(default_factory=TradingPairs)
    scanning: ScanningConfig = field(default_factory=ScanningConfig)
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    crash_detector: CrashDetectorConfig = field(default_factory=CrashDetectorConfig)
    key_levels: KeyLevelsConfig = field(default_factory=KeyLevelsConfig)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)


# Global config instance
config = Config()
