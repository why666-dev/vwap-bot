# ⚡ VWAP Trend Trading Bot

Automated paper trading bot implementing the **VWAP Trend Trading** strategy from:

> **"Volume Weighted Average Price (VWAP) — The Holy Grail for Day Trading Systems"**  
> Carlo Zarattini & Andrew Aziz, 2023 — [SSRN 4631351](https://ssrn.com/abstract=4631351)

---

## Strategy Rules (exact per paper)

| Rule | Detail |
|---|---|
| **Instrument** | QQQ + TQQQ (Nasdaq-100 ETFs) |
| **Timeframe** | 1-minute candles, RTH only |
| **VWAP formula** | `Σ(HLC3 × Volume) / Σ(Volume)` — resets each session |
| **Long signal** | 1-min candle closes **above** VWAP → BUY |
| **Short signal** | 1-min candle closes **below** VWAP → SELL SHORT |
| **First entry** | After 09:31 ET (first completed candle) |
| **Flip** | Candle closes on opposite side → flatten + reverse |
| **Stop** | Intracandle VWAP crosses **ignored** — candle close only |
| **Position size** | 100% of available equity per trade |
| **EOD exit** | All positions closed at **16:00 ET** — no overnight holds |
| **Commission** | $0.0005 / share (tracked in Excel log) |
| **Broker** | Alpaca paper trading (free) |

---

## Backtest Results (paper, 2018–2023)

| Strategy | Total Return | Sharpe | Max DD |
|---|---|---|---|
| VWAP TT (QQQ) | **671%** | 2.1 | 9.4% |
| VWAP TT (TQQQ) | **8,242%** | 1.7 | 36.1% |
| Buy & Hold QQQ | 126% | 0.7 | 35.6% |

---

## Project Structure

```
vwap-bot/
├── bot.py           # Core strategy engine
├── alerts.py        # Telegram alerts + Excel logger
├── server.py        # Flask API + WebSocket server
├── main.py          # Entry point
├── requirements.txt
├── .env.example     # Template — copy to .env
├── .env             # YOUR KEYS — never committed
└── .gitignore
```

---

## Setup

### 1. Clone & install
```bash
git clone https://github.com/YOUR_USERNAME/vwap-bot.git
cd vwap-bot
pip install -r requirements.txt
```

### 2. Alpaca paper account
1. Sign up free at [alpaca.markets](https://alpaca.markets)
2. Switch to **Paper Trading** (top-left dropdown)
3. Go to **API Keys** → Generate new key pair

### 3. Telegram bot (optional alerts)
1. Message **@BotFather** on Telegram → `/newbot` → copy token
2. Message **@userinfobot** → copy your numeric chat ID

### 4. Create `.env`
```bash
cp .env.example .env
# Edit .env with your real keys
```

```env
ALPACA_API_KEY=PKxxxxxxxxxxxxxxxx
ALPACA_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_TOKEN=1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789
```

### 5. Run
```bash
python main.py
```

Dashboard → `http://localhost:5050`  
Bot is silent outside market hours (09:30–16:00 ET).

---

## Deploying on a VPS (e.g. DigitalOcean / AWS EC2)

```bash
# On your server
git clone https://github.com/YOUR_USERNAME/vwap-bot.git
cd vwap-bot
pip install -r requirements.txt

# Create .env directly on server — never transfer via git
nano .env
# paste keys, Ctrl+X to save

# Run in background
nohup python main.py > output.log 2>&1 &

# Or use screen
screen -S vwapbot
python main.py
# Ctrl+A, D to detach
```

---

## Important Disclaimers

- This is **paper trading only** — no real money
- Past backtest results do not guarantee future performance  
- The paper itself states this is exploratory research
- Commission sensitivity is high — ensure low commission rates
- Not suitable for large accounts due to liquidity constraints

---

## References

Zarattini, C. & Aziz, A. (2023). *Volume Weighted Average Price (VWAP): The Holy Grail for Day Trading Systems*. SSRN Electronic Journal. https://ssrn.com/abstract=4631351
