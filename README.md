# Swing Trading Bot v2.15

Automated crypto swing trading bot (BTC, ETH, SOL, XRP, BNB) running on Bybit Spot. Scans every 15 minutes, scores setups using technical indicators + key levels, applies a regime filter (downtrend block + uptrend mode), and executes size-adjusted trades with DCA escalation, take-profit and trailing stop-loss.

---

## Architecture

```
bot.py              Main orchestrator — scan loop, trade execution, Telegram commands
config.py           All parameters in one place (indicators, scoring, risk, key levels)
strategy.py         Technical indicators + buy/sell scoring engine
risk_manager.py     Pre-trade risk checks (cooldowns, spread, DCA gates, drawdown)
exchange.py         Bybit API wrapper (spot, rate limiting, retry logic)
database.py         SQLite persistence (trades, positions, portfolio snapshots, state)
portfolio.py        Portfolio valuation and P&L tracking
notifications.py    Telegram alerts + interactive command handlers
params_updater.py   Daily job: dynamic key levels + ATR-based stops from recent candles
backtest.py         Backtest harness — replays Bybit klines through the real strategy
                    logic with per-feature config variants (see Backtesting below)
```

## Setup

**Requirements:** Python 3.11+, Bybit account, Telegram bot token.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python bot.py
```

**.env file:**
```
BYBIT_API_KEY=your_key
BYBIT_API_SECRET=your_secret
BYBIT_TESTNET=false
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

**Production (systemd):**
```
Server: root@159.223.8.250
Path:   /opt/dcabot/
Service: sudo systemctl restart swing-bot
Logs:   journalctl -u swing-bot -f
```

---

## How It Works

### Scan Cycle (every 15 min)
1. Fetch 4h OHLCV + daily candles for each trading pair
2. Compute indicators: RSI, Bollinger Bands, EMA (9/21/50/200), MACD, Volume
3. Score the setup (0–12+) against buy/sell signal criteria
4. Apply regime filter: block reversal buys in confirmed downtrend (price < EMA50
   falling AND < EMA200), unless a reversal signal fires (MACD cross / RSI leaving oversold)
5. Check risk rules (cooldowns, spread, crash detector, drawdown)
6. Execute trade sized by score and DCA level

### Regime Modes (v2.14/v2.15)
- **Downtrend** (price < EMA50 falling, < EMA200): reversal buys blocked; wider stop (×1.6).
- **Uptrend** (price > EMA200, EMA50 rising): overbought guard relaxed (RSI>70 only
  blocks if price is also above the upper Bollinger band), `rise_from_low` no longer
  scores as a sell signal, and adding to in-profit positions skips the −2% spread rule.
  Calibrated by 16-month backtest — see `Version History` and flags in `config.regime`.

### Buy Scoring
Each signal adds points. Thresholds:

| Score | Action | Capital |
|-------|--------|---------|
| ≤ 4 | No buy | — |
| 5 | Light buy | 8% of deployable |
| 6 | Moderate buy | 28% of deployable |
| 7–9 | Strong buy | 48% of deployable |
| 10+ | Maximum buy | 60% of deployable |

**Special gates:**
- Downtrend (below EMA 200): minimum score 6
- BTC: minimum score 6 always (`btc_min_buy_score`)

### DCA Escalation
When multiple entries are open for the same asset, subsequent entries are larger:
- 1st entry: base size
- 2nd entry: 1.5× base size (needs score ≥ 6)
- 3rd+ entry: 2.0× base size (needs score ≥ 7)

### Spread Guard
New entry must be ≥ 2% below the cheapest existing open entry. Prevents adding to a losing position too aggressively. **Exception (v2.15):** in confirmed uptrend with all open positions in profit, adds are allowed without the spread (pyramiding into strength).

### Stop-Loss / Take-Profit Logic
```
stop = max(entry × (1 - 0.035),  key_stop_level)     # ×1.6 wider below EMA200
take-profit: +3% closes the position
```
Higher price = tighter stop. `key_stop_level` in config is only effective for entries close to a structural level. Set it well below typical entry price to avoid hair-trigger stops.

### Trailing Stop
Activates at +1.5% profit, trails 0.8% below the peak. **Intentional quirk:** the trailing exit is only honored while P&L ≥ activation — positions that dip below +1.5% are allowed to breathe back toward the TP. Enforcing the trailing strictly was tested and performs far worse (see comment in `strategy.check_stop_loss`).

### Sell Scoring
| Score | Action | Size |
|-------|--------|------|
| ≤ 4 | No sell | — |
| 5–6 | Partial | 25% |
| 6–7 | Moderate | 50% |
| 8–9 | Strong | 75% |
| 10+ | Full exit | 100% |

---

## Key Configuration (config.py)

### Key Levels (update when market regime changes)
Manual support/resistance levels with score bonuses. Each level fires when price is within 0.8%.

```python
"BTCUSDT": {
    "supports":    [{"price": 74000, "score_bonus": 1}, ...],
    "resistances": [{"price": 76000, "score_bonus": 1}, ...],
    "stop_level":  70000,   # override for stop-loss floor
}
```

**Critical:** `stop_level` must be well below likely entry prices. At $74K BTC, keep it ≤ $70K so the 5% pct_stop always wins.

### Risk Limits
- `max_open_positions_per_asset`: 4 — hard cap on DCA levels
- `min_entry_spread_pct`: 2% — min gap between DCA entries (skipped in uptrend w/ positions in profit)
- `min_reserve_pct`: 10% — always keep 10% in USDT
- `cooldown_after_stop_loss`: 360 min (6h) — cooldown after stop trigger
- `max_drawdown_pct`: 12% — pauses bot if portfolio drops 12% from peak; auto-resumes when drawdown recovers to ≤ 6% (`auto_resume_*`)

---

## Telegram Commands

| Command | Action |
|---------|--------|
| `/status` | Current portfolio snapshot |
| `/positions` | Open positions with P&L |
| `/trades` | Recent trade history |
| `/pause` | Pause the bot |
| `/resume` | Resume scanning |
| `/force_sell BTCUSDT` | Market sell all of an asset |
| `/report` | Full performance report |

---

## Version History

| Version | Change |
|---------|--------|
| v2.15 | **Modo uptrend** validado por backtest 16m con atribución por pieza (`backtest.py`): guard de sobrecompra relajado en uptrend, re-entrada/piramidación con posiciones en ganancia sin spread −2%, `rise_from_low` fuera del sell score en uptrend. Baseline −3.45% → combo −1.38% (PF 1.09). Descartados con evidencia (flags apagados en config): pullback score (−8.8%), TP parcial con runner (−7.7%), stop 2.5% en uptrend, trailing estricto (−12.9%). |
| v2.14 | **Filtro de régimen** (no comprar reversión en downtrend confirmado) + **take-profit +3%** + stop −5%→−3.5% + trailing 2%→0.8% + auto-reanudación tras circuit breaker + cooldowns más cortos. Validado en backtest 16m (ver `docs/AUDITORIA_2026-06-27.md`). |
| v2.11 | Recalibrated key levels for May 23 regime; BTC stop_level 76K→70K |
| v2.10 | BTC minimum buy score = 6 (score-5 BTC historically underperforms) |
| v2.9 | DCA escalation multipliers, spread guard, score gates, opportunity-sized positions |
| v2.8 | Trailing stop activation lowered to +1.5% (was +3%, rarely triggered) |

---

## Backtesting

`backtest.py` replays historical Bybit klines through the **real** production logic (`strategy.py` scoring, risk gates, stops/TP/trailing intra-candle). Key levels and Fear & Greed are neutralized so no variant depends on hand-drawn levels over the past.

```bash
# data: <dir>/BTCUSDT_4h.json, BTCUSDT_daily.json, ... (Bybit v5 kline format)
python3 backtest.py --data-dir ./data                 # all variants
python3 backtest.py --data-dir ./data --variant combo # one variant
```

Variants map to the `config.regime.uptrend_*` flags and `risk.take_profit_partial`, enabling per-feature attribution. **Rule of the house: no strategy change ships without beating `baseline` here first.** Note: `calc_sell_score` takes an injectable `now` — without it, time-decay uses the wall clock and corrupts any historical replay.

---

## Database Schema

**`positions`** — open and closed positions  
**`trades`** — every executed buy/sell with score  
**`cooldowns`** — per-symbol cooldown expiry  
**`portfolio_snapshots`** — hourly portfolio value for drawdown tracking  
**`bot_state`** — key/value store (bot_status, pause_reason, etc.)

---

## Known Gotchas

- **SSH to server:** Key expires between sessions. Run `ssh-add ~/.ssh/id_ed25519` before connecting.
- **Service name:** `ssh` not `sshd` on Debian — `sudo systemctl restart ssh`
- **stop_level trap:** If a key level is close to entry price, `max(pct_stop, key_stop)` will use the key level, creating a very tight stop. Keep `stop_level` at least 5–7% below typical entry price.
- **6h stop-loss cooldown:** After a stop triggers, no buys for 6h on that symbol (was 24h until v2.14). Waterfall protection: ≥2 stops in 5 days → 48h pause.
- **DB column names:** `positions.stop_loss` (not `stop_loss_price`), `positions.qty` (not `quantity`)
- **Trailing "bug" that isn't:** the trailing stop is only honored while P&L ≥ +1.5%. Do not "fix" without backtesting — strict enforcement dropped the 16m backtest from −3.4% to −12.9%.
