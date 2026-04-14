# DeG£N$ — Autonomous Trading & Production System

> **Goal:** $500 → $10,000 in 20–30 days  
> **Method:** 74 bots (30 LAB trading + 10 MALL business + 34 research) running 24/7  
> **Safety:** Profit forcefield only moves up. Never lose 20%. Only stop on profit.

---

## Quick Start (5 minutes)

```bash
# 1. Clone and enter
git clone <your-repo>
cd DeG£N$

# 2. Install dependencies
pip install -r requirements.txt
pip install websocket-client   # for real-time Binance WebSocket feed

# 3. Copy and configure environment
cp .env.example .env
# Edit .env — add your API keys (most work without keys)

# 4. Start the system
python main.py
```

Open the dashboard at `http://localhost:8000`

---

## $500 → $10,000 Growth Playbook

### Capital Allocation (Day 1)
| Pool | Amount | Purpose |
|------|--------|---------|
| Tools reserve | $50 | API keys, hosting (never trade from this) |
| Mall bots | $50 | Self-funding business operations |
| Lab bots | $400 | Trading / betting / prediction markets |

### Weekly Milestones
| Week | Target | Daily % needed | Phase |
|------|--------|----------------|-------|
| Week 1 | $500 → $1,000 | +8%/day | SAFE / CAREFUL |
| Week 2 | $1k → $2,500 | +7%/day | CAREFUL / NORMAL |
| Week 3 | $2.5k → $5,000 | +6%/day | NORMAL / AGGRESSIVE |
| Week 4 | $5k → $10,000 | +5%/day | AGGRESSIVE / TURBO |

### Phase Ladder (auto-promoted by Wealth Manager)
| Bankroll | Phase | Kelly multiplier | Max single bet |
|----------|-------|-----------------|----------------|
| < $100 | FLOOR | 10% | 1% bankroll |
| $100–$200 | ULTRA_SAFE | 20% | 2% |
| $200–$350 | SAFE | 35% | 3% |
| $350–$600 | CAREFUL | 55% | 5% |
| $600–$1.5k | NORMAL | 80% | 8% |
| $1.5k–$4k | AGGRESSIVE | 100% | 10% |
| > $4k | TURBO | 120% | 12% |

### Safety Rules (non-negotiable)
- **Never lose >20% in a day** — circuit breaker halts all bots at 20% daily drawdown
- **Profit forcefield only moves up** — floor ratchets: +3%→break-even, +7%→+3%, +15%→+8%, +25%→+15%, +40%→+25%
- **Auto-vault at +10% daily** — 50% of gains above 10% locked to vault (unreachable by bots)
- **Kelly sizing** — maximum 10% bankroll per single bet (25% fractional Kelly default)

---

## Daily Maintenance (2 hours/day)

### Morning (30 min)
```bash
# Check overnight performance
curl http://localhost:8000/api/wealth/status

# Review any alerts in Telegram
# Check circuit breaker status
curl http://localhost:8000/api/circuit-breaker/status

# Reset daily vault floor
curl -X POST http://localhost:8000/api/wealth/reset-daily
```

### Midday (30 min)
```bash
# Review top lab bot performers
curl http://localhost:8000/api/lab/status

# Check mall bot production numbers
curl http://localhost:8000/api/mall/status

# Manual rebalance if needed
curl -X POST http://localhost:8000/api/portfolio/rebalance
```

### Evening (1 hour)
```bash
# Review daily summary (also sent to Telegram)
curl http://localhost:8000/api/wealth/playbook

# Check which bots are hot/cold
curl http://localhost:8000/api/portfolio/status

# Review signal accuracy by source
curl http://localhost:8000/api/decisions/stats

# Export daily trade log
curl http://localhost:8000/api/trades/today > logs/trades_$(date +%Y-%m-%d).json
```

---

## Environment Variables

### Required
```env
# Nothing is strictly required — all no-auth adapters work out of the box
# The system starts and runs in paper mode with zero API keys
```

### Recommended (unlock more data)
```env
TELEGRAM_BOT_TOKEN=<your_bot_token>   # get from @BotFather
TELEGRAM_CHAT_ID=<your_chat_id>       # get from @userinfobot
```

### Optional (higher quality signals)
```env
# News & sentiment
NEWSAPI_KEY=<key>         # newsapi.org — free tier 100 req/day
GNEWS_API_KEY=<key>       # gnews.io — free tier
CURRENTS_API_KEY=<key>    # currentsapi.services — free tier

# Financial data
ALPHA_VANTAGE_KEY=<key>   # alphavantage.co — free tier 25 req/day
FRED_API_KEY=<key>        # fred.stlouisfed.org — free, unlimited
CMC_API_KEY=<key>         # coinmarketcap.com — free tier

# Derivatives
COINGLASS_API_KEY=<key>   # coinglass.com — free tier

# Live trading (enable when ready)
KALSHI_API_KEY=<key>      # kalshi.com
KALSHI_API_SECRET=<key>
POLYMARKET_PRIVATE_KEY=<key>
```

### System flags
```env
PAPER_MODE=true           # set false for live trading (do this gradually!)
STARTING_CAPITAL=500      # your actual starting capital
PHASE=safe                # starting phase (auto-managed after this)
```

---

## Architecture Overview

```
+-------------------------------------------------------------------+
|                        MAIN ENTRY POINT                           |
|                          main.py / server.py                      |
+----------------+---------------------+----------------------------+
                 |                     |
         +-------+------+     +--------+--------+
         | LAB LANE     |     |  MALL LANE      |
         | 30 trading/  |     |  10 business/   |
         | prediction   |     |  content bots   |
         | bots         |     |                 |
         +-------+------+     +--------+--------+
                 |                     |
         +-------+---------------------+-------+
         |           SERVICES LAYER            |
         |  DecisionEngine  <-> SignalAggregator|
         |  KellySizer      <-> CircuitBreaker  |
         |  ProfitForcefield<-> PortfolioAlloc  |
         |  RealTimeEngine  <-> HealthMonitor   |
         +--------------------------------------+
                 |
         +-------+--------------------------------------+
         |           ADAPTERS (40+)                     |
         |  Binance WS  CoinGecko  Kalshi              |
         |  Polymarket  DeFiLlama  Metaculus            |
         |  ForexRates  WorldMkts  Fear&Greed           |
         +----------------------------------------------+
                 |
         +-------+--------------------------------------+
         |  WEALTH MANAGER + VAULT                      |
         |  Phase auto-promotion, daily reset,          |
         |  milestone alerts, Telegram reports          |
         +----------------------------------------------+
```

---

## System Components

### Adapters (40 total)
| Adapter | Data | Auth |
|---------|------|------|
| `binance_public` | Spot prices, klines, funding rates | None |
| `binance_ws` | Real-time WebSocket feed (<100ms) | None |
| `coingecko` | OHLCV, market cap, trending | None |
| `coincap` | Real-time crypto prices | None |
| `fear_greed` | Crypto Fear & Greed index | None |
| `defillama` | DeFi TVL, yields, stablecoins | None |
| `coinglass` | Funding rates, liquidations, OI | Optional |
| `forex_rates` | 37 fiat currencies (NGN, GHS, ZAR, BRL, JMD...) | None |
| `world_markets` | 27 global indices (S&P, Nikkei, Tadawul, Sensex...) | None |
| `kalshi_public` | Prediction market prices | None |
| `polymarket_public` | Prediction market prices | None |
| `metaculus` | Forecasting platform consensus | None |
| `thesportsdb` | Sports results and leagues | None |
| `ergast_f1` | F1 race data | None |
| `sec_edgar` | Insider filings, 13F | None |
| `fred_api` | Macro data (CPI, unemployment, etc.) | Optional |
| `newsapi` | News headlines | Optional |
| + 23 more | Various | See adapters/ |

### Services (48 total)
| Service | Purpose |
|---------|---------|
| `decision_engine` | Signal to action in <50ms |
| `realtime_engine` | WebSocket + REST price feeds |
| `signal_aggregator` | Weighted ensemble voting |
| `kelly_sizer` | Kelly + phase-aware bet sizing |
| `portfolio_allocator` | Dynamic capital allocation across 40 bots |
| `profit_forcefield` | Trailing profit lock (ratchets up only) |
| `circuit_breaker` | Loss limit + velocity + daily halt |
| `lab_orchestrator` | Runs all 30 lab bots, feeds DecisionEngine |
| `mall_production_engine` | Runs all 10 mall bots, tracks revenue |
| `math_engine` | Optimal-F, Kelly bands, Monte Carlo |
| `backtester` | Walk-forward, significance testing |
| `technical_indicators` | SMA, EMA, RSI, MACD, BB, ATR, VWAP |
| `vader_sentiment` | Local NLP sentiment scoring |
| `health_monitor` | Concurrent adapter health checks |
| `security_middleware` | Rate limits, input sanitisation, HMAC |
| `cache_service` | L1 memory + L2 Redis TTL cache |
| `db_hygiene` | SQLite vacuum, purge, index health |

### Bots (74 total)
**LAB bots (30)** — Prediction markets, crypto, macro, sports  
**MALL bots (10)** — Shopify, Etsy, eBay, affiliate, digital downloads, newsletter, YouTube, podcast, freelance, job board  
**RESEARCH bots (34)** — Data gathering, signal generation, watchlist management  

---

## Go-Live Checklist

### Week 1 — Paper Mode Validation ($0 real money)
- [ ] System runs 24h without crashes: `python main.py`
- [ ] Telegram alerts firing correctly
- [ ] At least 20 lab bot signals/hour in paper mode
- [ ] Circuit breaker test: manually trigger daily loss in paper
- [ ] Health monitor shows 15+ adapters green
- [ ] Daily P&L shown in dashboard

### Week 2 — Dust Live ($5 real bets)
- [ ] Fund Kalshi account with $20 (start here — cleanest prediction markets)
- [ ] Set `PAPER_MODE=false` for Kalshi bots only
- [ ] `PHASE=ultra_safe` — max $0.50/bet
- [ ] Watch 10 live bets, verify circuit breaker catches losses
- [ ] Enable Polymarket bots with $10

### Week 3 — Capped Live ($50 real bets)
- [ ] `PHASE=safe` — max $3/bet
- [ ] Enable crypto momentum bot with $20 on Binance
- [ ] Enable 3 top lab bots from paper performance ranking
- [ ] Mall bots: publish first 10 digital products on Gumroad/Etsy
- [ ] First vault deposit locked

### Week 4 — Full Deployment ($200+ active)
- [ ] `PHASE=careful` or higher based on actual performance
- [ ] All top-10 lab bots live
- [ ] Mall bots generating $5-15/day revenue
- [ ] Auto-rebalance running every 5 minutes
- [ ] Daily Telegram summary confirmed working

---

## API Endpoints

```bash
# System health
GET  /api/health

# Wealth management
GET  /api/wealth/status          # current bankroll, phase, progress
GET  /api/wealth/playbook        # $500 to $10k progress breakdown
POST /api/wealth/reset-daily     # reset daily floor (call each morning)

# Lab bots
GET  /api/lab/status             # orchestrator status
GET  /api/lab/last-cycle         # most recent cycle signals + decisions
POST /api/lab/run-once           # trigger single manual cycle

# Mall bots
GET  /api/mall/status            # engine status
GET  /api/mall/leaderboard       # bots ranked by revenue
GET  /api/mall/daily-stats       # today's ops + revenue

# Portfolio
GET  /api/portfolio/status       # allocations, hot/cold bots, top performers
POST /api/portfolio/rebalance    # trigger manual rebalance

# Circuit breaker
GET  /api/circuit-breaker/status # all bot breaker states
POST /api/circuit-breaker/reset  # reset all breakers (use after fixing cause)

# Price feeds
GET  /api/prices                 # all live prices
GET  /api/prices/{symbol}        # single symbol
GET  /api/forex                  # 37 currency pairs
GET  /api/markets                # global stock indices

# Decisions
GET  /api/decisions/stats        # accuracy, confidence, P&L by source
```

---

## Troubleshooting

### System won't start
```bash
# Check Python version (needs 3.11+)
python --version

# Install missing deps
pip install -r requirements.txt

# Check for import errors
python -c "import main"
```

### No signals being generated
```bash
# Check adapter health
curl http://localhost:8000/api/health

# Verify at least 2 sources active (MIN_SIGNALS_TO_ACT = 2)
curl http://localhost:8000/api/decisions/stats

# Run a manual lab cycle
curl -X POST http://localhost:8000/api/lab/run-once
```

### Circuit breaker stuck open
```bash
# Check what triggered it
curl http://localhost:8000/api/circuit-breaker/status

# Reset after reviewing (do not reset during a real loss streak)
curl -X POST http://localhost:8000/api/circuit-breaker/reset
```

### Real-time prices not updating
```bash
# If websocket-client not installed:
pip install websocket-client

# System falls back to REST polling at 2s intervals automatically
# Verify via:
curl http://localhost:8000/api/prices
```

### Telegram not sending
```bash
# Test your bot token
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe"

# Test a message
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d "chat_id=${TELEGRAM_CHAT_ID}&text=test"
```

---

## Performance Benchmarks

| Metric | Target | Typical |
|--------|--------|---------|
| Signal to decision latency | <50ms | 8-25ms |
| WebSocket price feed latency | <100ms | 20-60ms |
| REST fallback poll interval | 2s | 2s |
| Lab bot cycle time (fast) | <10s | 3-8s |
| Lab bot cycle time (full) | <30s | 12-25s |
| Mall bot daily ops | 1,000+ | 800-1,600 |
| Portfolio rebalance | <500ms | 80-200ms |
| Health check (40 adapters) | <15s | 6-12s |

---

## Cost Optimisation

The system is designed to run **free** on a base level:
- 40 adapters work with zero API keys
- Binance WebSocket is free and unlimited
- All NLP runs locally (VADER, no OpenAI calls needed for signals)
- SQLite by default (no PostgreSQL cost)
- Can run on a $5/mo VPS (512MB RAM minimum, 1GB recommended)

### Estimated monthly costs (all optional)
| Service | Cost | Benefit |
|---------|------|---------|
| Render/Fly hosting | $7/mo | 24/7 uptime vs local |
| Newsapi.org paid | $49/mo | Better news signals |
| Alpha Vantage paid | $50/mo | Real-time stock data |
| OpenAI API | ~$5/mo | Enhanced content for mall bots |
| **Total optional** | **~$111/mo** | **Improves signal quality** |

---

## Security

- All API keys loaded from `.env` (never committed)
- Input sanitisation on all external data (SQL injection, XSS, path traversal)
- HMAC signing for internal API calls
- Rate limiting: 100 req/min per IP on REST endpoints
- Secret redaction in all log output
- Strict CSP / HSTS headers on dashboard

---

*Built with Python 3.11+ | FastAPI | SQLite/PostgreSQL | Telegram*
