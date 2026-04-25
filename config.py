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
    buy_light: int = 5                  # Score 5-6: buy 10%
    buy_moderate: int = 7               # Score 7-8: buy 20%
    buy_strong: int = 8                 # Score 8-9: buy 30%
    buy_maximum: int = 10               # Score 10+: buy 40%

    # Buy capital allocation (% of available) — wider spread to concentrate on high-conviction
    buy_light_pct: float = 0.10          # Score 5-6: small position (marginal signal)
    buy_moderate_pct: float = 0.30       # Score 7: meaningful (100% win rate historically)
    buy_strong_pct: float = 0.45         # Score 8-9: aggressive
    buy_maximum_pct: float = 0.60        # Score 10+: max conviction

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
class RiskConfig:
    """Risk management parameters."""
    # Stop-loss
    stop_loss_pct: float = 0.05         # -5% from entry
    trailing_stop_activation: float = 0.015  # Activate trailing at +1.5% (was 3% — never triggered)
    trailing_stop_distance: float = 0.02    # 2% below max

    # Position sizing
    max_per_trade_pct: float = 0.40     # Max 40% of available per trade
    min_reserve_pct: float = 0.10       # Always keep 10% in USDT
    min_order_usdt: float = 10.0        # Bybit minimum
    max_open_positions_per_asset: int = 10

    # Portfolio limits
    max_daily_loss_pct: float = 0.05    # -5% daily circuit breaker
    max_drawdown_pct: float = 0.15      # -15% total circuit breaker

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
    Optional stop_level overrides percentage-based stop-loss."""
    levels: dict = field(default_factory=lambda: {
        "BTCUSDT": {
            # Updated Apr 15 — BTC at $75,028
            "supports": [
                {"price": 73000,  "label": "Recent support zone",             "score_bonus": 1},
                {"price": 71000,  "label": "Prior breakout support",          "score_bonus": 2},
                {"price": 69000,  "label": "Key support (accumulation)",      "score_bonus": 2},
                {"price": 67000,  "label": "Deep structural support",         "score_bonus": 3},
            ],
            "resistances": [
                {"price": 76500,  "label": "Recent swing high",       "score_bonus": 1},
                {"price": 78000,  "label": "Psychological level",     "score_bonus": 2},
                {"price": 80000,  "label": "Major round number",      "score_bonus": 2},
            ],
            "stop_level": 67000,
        },
        "ETHUSDT": {
            # Updated Apr 15 — ETH at $2,372
            "supports": [
                {"price": 2300, "label": "Recent support zone",      "score_bonus": 1},
                {"price": 2200, "label": "Prior resistance → support", "score_bonus": 2},
                {"price": 2100, "label": "Strong support",            "score_bonus": 2},
                {"price": 2000, "label": "Deep structural support",   "score_bonus": 3},
            ],
            "resistances": [
                {"price": 2500, "label": "Psychological level",    "score_bonus": 1},
                {"price": 2650, "label": "Range high target",      "score_bonus": 2},
                {"price": 2800, "label": "Major resistance",       "score_bonus": 2},
            ],
            "stop_level": 2000,
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
    key_levels: KeyLevelsConfig = field(default_factory=KeyLevelsConfig)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)


# Global config instance
config = Config()
