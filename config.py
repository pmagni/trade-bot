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
    """Trading pair configuration."""
    symbols: list = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    base_assets: list = field(default_factory=lambda: ["BTC", "ETH"])
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
    consecutive_sl_pause_hours: int = 168 # 7 días de pausa tras waterfall detectado

    # Stop-loss más ancho cuando el precio está bajo EMA200 (downtrend)
    # Evita "gapping" — ser forzado a salir muy por debajo del stop calculado
    # entre scans de 5 min. En uptrend: stop = 5%. En downtrend: 5% × 1.6 = 8%
    downtrend_stop_multiplier: float = 1.6   # multiplicador del stop_loss_pct en bear market


@dataclass
class RiskConfig:
    """Risk management parameters."""
    # Stop-loss
    stop_loss_pct: float = 0.05         # -5% from entry (uptrend); ×1.6 en downtrend (v2.12)
    trailing_stop_activation: float = 0.015  # Activate trailing at +1.5% (was 3% — never triggered)
    trailing_stop_distance: float = 0.02    # 2% below max

    # Position sizing
    max_per_trade_pct: float = 0.40     # Max 40% of available per trade
    min_reserve_pct: float = 0.10       # Always keep 10% in USDT
    min_order_usdt: float = 10.0        # Bybit minimum
    max_open_positions_per_asset: int = 4   # was 10 — cap DCA at 4 levels
    min_entry_spread_pct: float = 0.02      # New entry must be ≥2% below cheapest open entry

    # Portfolio limits
    max_daily_loss_pct: float = 0.05    # -5% daily circuit breaker
    max_drawdown_pct: float = 0.12      # -12% total circuit breaker (era 15% — activaba tarde)

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
    cooldown_after_stop_loss: int = 1440  # 24 hours
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
    updated_at: str = "2026-06-06"  # Actualizar al modificar los niveles

    levels: dict = field(default_factory=lambda: {
        "BTCUSDT": {
            # Updated Jun 6 — BTC en ~$60,500 (crash desde $80K; todos los soportes anteriores rotos)
            # stop_level debe quedar al menos 8% por debajo de la entrada esperada.
            # Con BTC en $60K y entradas en $58-62K → stop $53K = aprox -8.6% del entry.
            "supports": [
                {"price": 58000, "label": "Piso actual / soporte reciente",    "score_bonus": 1},
                {"price": 55000, "label": "Zona de acumulación previa",        "score_bonus": 2},
                {"price": 52000, "label": "Soporte estructural fuerte",        "score_bonus": 3},
                {"price": 48000, "label": "Acumulacion profunda / ATH-2021",   "score_bonus": 3},
            ],
            "resistances": [
                {"price": 63000, "label": "Soporte roto -> resistencia",       "score_bonus": 1},
                {"price": 66000, "label": "Zona de consolidacion previa",      "score_bonus": 2},
                {"price": 69000, "label": "Resistencia clave 2024",            "score_bonus": 2},
            ],
            "stop_level": 53000,
        },
        "ETHUSDT": {
            # Updated Jun 6 — ETH en ~$1,554 (crash desde $2,600; $2K, $1,800 y $1,650 rotos)
            # stop_level debe quedar al menos 8% por debajo de la entrada esperada.
            # Con ETH en $1,550 y entradas en $1,450-1,550 → stop $1,350 = aprox -8.7%.
            "supports": [
                {"price": 1500, "label": "Soporte psicologico / piso actual",  "score_bonus": 2},
                {"price": 1400, "label": "Zona de acumulacion 2023",           "score_bonus": 2},
                {"price": 1250, "label": "Soporte estructural fuerte",         "score_bonus": 3},
                {"price": 1100, "label": "Acumulacion profunda",               "score_bonus": 3},
            ],
            "resistances": [
                {"price": 1650, "label": "Soporte roto -> resistencia",        "score_bonus": 1},
                {"price": 1780, "label": "Zona de consolidacion previa",       "score_bonus": 2},
                {"price": 1900, "label": "Resistencia mayor",                  "score_bonus": 2},
            ],
            "stop_level": 1350,
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
    crash_detector: CrashDetectorConfig = field(default_factory=CrashDetectorConfig)
    key_levels: KeyLevelsConfig = field(default_factory=KeyLevelsConfig)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)


# Global config instance
config = Config()
