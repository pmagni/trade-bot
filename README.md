# Swing Trading Bot v2.11

Automated BTC/ETH swing trading bot running on Bybit Spot. Scans every 15 minutes, scores setups using technical indicators + key levels, and executes size-adjusted trades with DCA escalation and trailing stop-loss.

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
1. Fetch 4h OHLCV + daily candles for BTCUSDT and ETHUSDT
2. Compute indicators: RSI, Bollinger Bands, EMA (9/21/200), MACD, Volume
3. Score the setup (0–12+) against buy/sell signal criteria
4. Check risk rules (cooldowns, spread, drawdown)
5. Execute trade sized by score and DCA level

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
New entry must be ≥ 2% below the cheapest existing open entry. Prevents adding to a losing position too aggressively.

### Stop-Loss Logic
```
stop = max(entry × (1 - 0.05),  key_stop_level)
```
Higher price = tighter stop. `key_stop_level` in config is only effective for entries close to a structural level. Set it well below typical entry price to avoid hair-trigger stops.

### Trailing Stop
Activates at +1.5% profit, trails 2% below the peak. Locks in gains on runners.

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
- `min_entry_spread_pct`: 2% — min gap between DCA entries
- `min_reserve_pct`: 10% — always keep 10% in USDT
- `cooldown_after_stop_loss`: 1440 min (24h) — circuit breaker after stop trigger
- `max_drawdown_pct`: 15% — pauses bot if portfolio drops 15% from peak

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
| v2.14 | **Filtro de régimen** (no comprar reversión en downtrend confirmado) + **take-profit +3%** + stop −5%→−3.5% + trailing 2%→0.8% + auto-reanudación tras circuit breaker + cooldowns más cortos. Validado en backtest 16m (ver `docs/AUDITORIA_2026-06-27.md`). |
| v2.11 | Recalibrated key levels for May 23 regime; BTC stop_level 76K→70K |
| v2.10 | BTC minimum buy score = 6 (score-5 BTC historically underperforms) |
| v2.9 | DCA escalation multipliers, spread guard, score gates, opportunity-sized positions |
| v2.8 | Trailing stop activation lowered to +1.5% (was +3%, rarely triggered) |

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
- **24h stop-loss cooldown:** After a stop triggers, no buys for 24h on that symbol. Best setups can appear during this window — check `/status` before manual intervention.
- **DB column names:** `positions.stop_loss` (not `stop_loss_price`), `positions.qty` (not `quantity`)
