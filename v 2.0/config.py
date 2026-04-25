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
    base_url: str = "https://api-testnet.bybit.com" if os.getenv("BYBIT_TESTNET", "false").lower() == "true" else "https://api.bybit.com"


@dataclass
class TelegramConfig:
    """Telegram bot configuration."""
    token: str = os.getenv("TELEGRAM_TOKEN", "")
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
    interval_minutes: int = 15          # How often to scan
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
    buy_no_action: int = 3              # Score 0-3: don't buy
    buy_light: int = 4                  # Score 4-5: buy 10%
    buy_moderate: int = 6               # Score 6-7: buy 20%
    buy_strong: int = 8                 # Score 8-9: buy 30%
    buy_maximum: int = 10               # Score 10+: buy 40%

    # Buy capital allocation (% of available)
    buy_light_pct: float = 0.10
    buy_moderate_pct: float = 0.20
    buy_strong_pct: float = 0.30
    buy_maximum_pct: float = 0.40

    # Sell score thresholds
    sell_no_action: int = 3
    sell_partial: int = 4               # Score 4-5: sell 25%
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


@dataclass
class RiskConfig:
    """Risk management parameters."""
    # Stop-loss
    stop_loss_pct: float = 0.07         # -7% from entry
    trailing_stop_activation: float = 0.05  # Activate trailing at +5%
    trailing_stop_distance: float = 0.03    # 3% below max

    # Position sizing
    max_per_trade_pct: float = 0.40     # Max 40% of available per trade
    min_reserve_pct: float = 0.20       # Always keep 20% in USDT
    min_order_usdt: float = 10.0        # Bybit minimum
    max_open_positions_per_asset: int = 3

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

    # Cooldowns (in minutes)
    cooldown_after_buy: int = 240       # 4 hours
    cooldown_after_sell: int = 120      # 2 hours
    cooldown_after_stop_loss: int = 1440  # 24 hours
    max_trades_per_day_per_asset: int = 3


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
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)


# Global config instance
config = Config()
