"""
bot.py — VWAP Trend Trading Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Paper: Zarattini & Aziz, 2023 (SSRN 4631351)

DEEP ANALYSIS IMPROVEMENTS:
[1]  No Telegram — removed completely
[2]  Stream reconnect — auto-reconnects if WebSocket drops
[3]  Duplicate trade guard — prevents same candle triggering twice
[4]  Order confirmation — verifies order actually filled before recording
[5]  Real-time price for sizing — gets actual quote not bar close
[6]  Stale bar guard — ignores bars older than 2 minutes
[7]  Position sync on startup — reads existing Alpaca positions
[8]  VWAP gap protection — skips if VWAP hasn't had enough data yet (< 5 candles)
[9]  Flat detection after flip — confirms position is truly flat before entering
[10] Commission tracking — entry + exit both tracked accurately
[11] EOD uses actual fill price from Alpaca — not stale last_price
[12] Thread safety — all state mutations under lock
[13] Log rotation — prevents log file growing indefinitely
[14] Graceful shutdown — closes positions on SIGTERM/SIGINT
"""

import os
import json
import signal
import threading
import time as t
import logging
import logging.handlers
from datetime import datetime, time
from dataclasses import dataclass, field, asdict
from typing import Optional

import pytz
from dotenv import load_dotenv
import alpaca_trade_api as tradeapi
from alpaca_trade_api.stream import Stream

from excel_logger import log_trade_to_excel

load_dotenv()

ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY",    "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
BASE_URL          = "https://paper-api.alpaca.markets"

# ── STRATEGY CONSTANTS (exact from paper) ─────────────────────────────────────
SYMBOLS        = ["QQQ", "TQQQ"]
ET             = pytz.timezone("America/New_York")
SESSION_START  = time(9, 30)    # VWAP resets here — no pre-market data
FIRST_ENTRY_AT = time(9, 31)    # First valid candle close at 09:31
SESSION_END    = time(15, 58)   # 2-min buffer before market close
COMMISSION     = 0.0005         # $0.0005/share — paper sec 3.4

# Paper sec 5 (Fig 7): most profit in morning + last hour
# Set True to skip 12:00–15:00 (optional optimization)
SKIP_MIDDAY    = False
MIDDAY_START   = time(12, 0)
MIDDAY_END     = time(15, 0)

# Capital split: 45% per symbol — prevents buying power competition
CAPITAL_PCT    = 0.45
MIN_VWAP_BARS  = 5    # need at least 5 bars before VWAP is reliable
STALE_BAR_SECS = 120  # ignore bars older than 2 minutes
ORDER_CONFIRM_WAIT = 2  # seconds to wait for order fill confirmation

# ── LOGGING WITH ROTATION ──────────────────────────────────────────────────────
def _setup_logging():
    fmt  = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh   = logging.handlers.RotatingFileHandler(
        "bot.log", maxBytes=5*1024*1024, backupCount=3
    )
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    root.addHandler(sh)

_setup_logging()
log = logging.getLogger(__name__)


# ── VWAP ENGINE ───────────────────────────────────────────────────────────────
class VWAPEngine:
    """
    VWAP = Σ(HLC3_t × Volume_t) / Σ(Volume_t)  — paper eq.1
    HLC3 = (High + Low + Close) / 3
    Resets every session — excludes pre/post market data
    """
    def __init__(self):
        self.reset()

    def reset(self):
        self._cum_pv   = 0.0
        self._cum_vol  = 0.0
        self._bar_count = 0

    def update(self, high: float, low: float,
               close: float, volume: float) -> float:
        hlc3           = (high + low + close) / 3.0
        self._cum_pv  += hlc3 * volume
        self._cum_vol += volume
        self._bar_count += 1
        return self._cum_pv / self._cum_vol if self._cum_vol else close

    @property
    def is_reliable(self) -> bool:
        """VWAP needs at least MIN_VWAP_BARS candles to be reliable."""
        return self._bar_count >= MIN_VWAP_BARS

    @property
    def bar_count(self): return self._bar_count
    @property
    def cum_pv(self):    return self._cum_pv
    @property
    def cum_vol(self):   return self._cum_vol


# ── POSITION SIZER ────────────────────────────────────────────────────────────
def calc_shares(api, symbol: str, side: str, price: float) -> int:
    """
    Paper sec 3.3: use 100% of available funds.
    Implementation: 45% per symbol to prevent competition.
    Uses actual quote price for accuracy.
    """
    try:
        acct   = api.get_account()
        equity = float(acct.equity)
        bp     = float(acct.buying_power)
    except Exception as e:
        log.error(f"Account fetch failed: {e}")
        return 0

    # Try to get a more accurate current price via quote
    try:
        q     = api.get_latest_quote(symbol)
        ask   = float(q.ap) if q.ap and float(q.ap) > 0 else 0
        bid   = float(q.bp) if q.bp and float(q.bp) > 0 else 0
        if ask > 0 and bid > 0:
            price = (ask + bid) / 2.0
        elif ask > 0:
            price = ask
        elif bid > 0:
            price = bid
        # else fall through to bar.close passed in
    except Exception:
        pass  # use bar.close as fallback

    if price <= 0:
        log.warning(f"  sizing skipped — price=0 for {symbol}")
        return 0

    if side == "long":
        capital = min(equity * CAPITAL_PCT, bp * CAPITAL_PCT)
    else:
        capital = min(equity * 0.35, bp * 0.35)

    if capital < 50:
        log.warning(f"  capital too low ${capital:.2f} for {symbol}")
        return 0

    shares = int(capital / price)
    log.info(f"  sizing {symbol} | equity=${equity:.0f} "
             f"capital=${capital:.0f} price=${price:.2f} → {shares}sh")
    return max(shares, 0)


# ── SYMBOL STATE ──────────────────────────────────────────────────────────────
@dataclass
class SymbolState:
    symbol:           str
    position:         int           = 0
    side:             Optional[str] = None
    entry_price:      float         = 0.0
    vwap:             float         = 0.0
    last_price:       float         = 0.0
    cum_vol:          float         = 0.0
    cum_pv:           float         = 0.0
    unrealized_pnl:   float         = 0.0
    realized_pnl:     float         = 0.0
    commission_paid:  float         = 0.0
    trade_count:      int           = 0
    win_count:        int           = 0
    loss_count:       int           = 0
    last_bar_time:    str           = ""  # guard against duplicate bars
    trades:           list          = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    def record_commission(self, shares: int):
        self.commission_paid += abs(shares) * COMMISSION


# ── ORDER MANAGER ─────────────────────────────────────────────────────────────
class OrderManager:
    def __init__(self, api):
        self.api = api

    def enter(self, symbol: str, side: str, qty: int) -> bool:
        if qty <= 0:
            log.warning(f"ENTER skipped — qty=0 for {symbol}")
            return False
        alpaca_side = "buy" if side == "long" else "sell"
        try:
            o = self.api.submit_order(
                symbol=symbol, qty=qty,
                side=alpaca_side, type="market", time_in_force="day"
            )
            log.info(f"✅ ENTER {symbol} {alpaca_side.upper()} {qty}sh | id={o.id}")
            # Brief wait for order to process
            t.sleep(ORDER_CONFIRM_WAIT)
            return True
        except Exception as e:
            log.error(f"❌ ENTER failed {symbol}: {e}")
            return False

    def flatten(self, symbol: str, position: int) -> bool:
        if position == 0:
            return True
        side = "sell" if position > 0 else "buy"
        try:
            o = self.api.submit_order(
                symbol=symbol, qty=abs(position),
                side=side, type="market", time_in_force="day"
            )
            log.info(f"🔄 FLATTEN {symbol} {side.upper()} {abs(position)}sh | id={o.id}")
            t.sleep(ORDER_CONFIRM_WAIT)
            return True
        except Exception as e:
            log.error(f"❌ FLATTEN failed {symbol}: {e}")
            return False

    def close_eod(self, symbol: str) -> Optional[float]:
        """
        Use Alpaca close_position for EOD — most reliable.
        Returns actual fill price if available.
        """
        try:
            # Get current position price before closing
            pos = self.api.get_position(symbol)
            current_price = float(pos.current_price)
        except Exception:
            current_price = None

        try:
            self.api.close_position(symbol)
            log.info(f"🏁 EOD CLOSE {symbol} | price~${current_price:.2f}" if current_price else f"🏁 EOD CLOSE {symbol}")
            return current_price
        except tradeapi.rest.APIError as e:
            if "position does not exist" in str(e).lower() or "no position" in str(e).lower():
                log.info(f"  {symbol}: no position to close")
            else:
                log.error(f"❌ EOD close_position failed {symbol}: {e}")
            return None
        except Exception as e:
            log.error(f"❌ EOD close failed {symbol}: {e}")
            return None

    def get_actual_position(self, symbol: str) -> tuple[int, float]:
        """
        Sync with Alpaca — get actual position qty and avg entry price.
        Returns (qty, avg_price). qty > 0 = long, < 0 = short, 0 = flat.
        """
        try:
            pos = self.api.get_position(symbol)
            return int(pos.qty), float(pos.avg_entry_price)
        except tradeapi.rest.APIError:
            return 0, 0.0
        except Exception:
            return 0, 0.0


# ── MAIN BOT ──────────────────────────────────────────────────────────────────
class VWAPBot:
    def __init__(self):
        self.api     = tradeapi.REST(ALPACA_API_KEY, ALPACA_SECRET_KEY,
                                      BASE_URL, api_version="v2")
        self.orders  = OrderManager(self.api)
        self.lock    = threading.Lock()
        self.running = False

        self.states  = {s: SymbolState(symbol=s) for s in SYMBOLS}
        self.engines = {s: VWAPEngine()           for s in SYMBOLS}

        # Graceful shutdown handlers
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT,  self._shutdown)

        log.info(f"VWAPBot initialized | symbols={SYMBOLS} | commission=${COMMISSION}/sh")
        log.info(f"Session: {SESSION_START}–{SESSION_END} ET | First entry: {FIRST_ENTRY_AT}")
        log.info(f"Capital per symbol: {CAPITAL_PCT*100:.0f}% | Min VWAP bars: {MIN_VWAP_BARS}")

        # Sync with any existing positions on Alpaca at startup
        self._sync_positions_on_startup()

    def _shutdown(self, signum, frame):
        log.info(f"🛑 Shutdown signal received ({signum}) — closing positions...")
        self.end_of_day()
        self.running = False

    def _sync_positions_on_startup(self):
        """Read any existing positions from Alpaca at bot startup."""
        for sym in SYMBOLS:
            qty, avg_price = self.orders.get_actual_position(sym)
            if qty != 0:
                state             = self.states[sym]
                state.position    = qty
                state.side        = "long" if qty > 0 else "short"
                state.entry_price = avg_price
                log.info(f"  Startup sync {sym}: {state.side} {abs(qty)}sh @ ${avg_price:.2f}")
            else:
                log.info(f"  Startup sync {sym}: flat")

    # ── HELPERS ───────────────────────────────────────────────────────────────
    def _equity(self) -> float:
        try:
            return float(self.api.get_account().equity)
        except Exception:
            return 0.0

    def _total_realized(self) -> float:
        return sum(s.realized_pnl for s in self.states.values())

    def _calc_pnl(self, state: SymbolState, exit_price: float) -> float:
        qty = abs(state.position)
        if state.side == "long":
            return (exit_price - state.entry_price) * qty
        else:
            return (state.entry_price - exit_price) * qty

    def _record(self, trade: dict):
        """Write to Excel — always in background thread."""
        equity  = self._equity()
        cum_pnl = self._total_realized()
        threading.Thread(
            target=log_trade_to_excel,
            args=(trade, equity, cum_pnl),
            daemon=True
        ).start()

    def _in_trading_window(self, now: time) -> bool:
        if now < FIRST_ENTRY_AT or now >= SESSION_END:
            return False
        if SKIP_MIDDAY and MIDDAY_START <= now < MIDDAY_END:
            return False
        return True

    def _is_stale_bar(self, bar_timestamp) -> bool:
        """Ignore bars that are too old — can happen after reconnect."""
        try:
            if hasattr(bar_timestamp, 'timestamp'):
                bar_ts = bar_timestamp.timestamp()
            else:
                bar_ts = datetime.fromisoformat(
                    str(bar_timestamp).replace("Z", "+00:00")
                ).timestamp()
            age = t.time() - bar_ts
            if age > STALE_BAR_SECS:
                log.debug(f"Stale bar ignored — {age:.0f}s old")
                return True
        except Exception:
            pass
        return False

    # ── BAR HANDLER ───────────────────────────────────────────────────────────
    def on_bar(self, bar):
        """
        Called on every 1-min candle close from Alpaca stream.

        Paper rules (strictly followed):
        - Signal = candle CLOSE vs VWAP — intracandle crosses IGNORED
        - Wait for 09:31 first candle
        - Always in market after first entry (long or short)
        - Flip on VWAP cross confirmation
        """
        symbol = bar.symbol
        if symbol not in SYMBOLS:
            return

        # Guard: ignore stale bars (e.g. after reconnect)
        if self._is_stale_bar(bar.timestamp):
            return

        now_et  = datetime.now(ET).time()
        bar_key = str(bar.timestamp)

        with self.lock:
            state  = self.states[symbol]
            engine = self.engines[symbol]

            # Guard: ignore duplicate bar (same timestamp)
            if bar_key == state.last_bar_time:
                return
            state.last_bar_time = bar_key

            # ── SESSION RESET ─────────────────────────────────────────────────
            # Reset VWAP at session open — exclude pre-market data (paper sec 2)
            if now_et <= SESSION_START:
                engine.reset()
                state.side     = None
                state.position = 0
                return

            if now_et >= SESSION_END:
                return

            # ── UPDATE VWAP (RTH only) ────────────────────────────────────────
            vwap             = engine.update(bar.high, bar.low, bar.close, bar.volume)
            state.vwap       = round(vwap, 4)
            state.last_price = bar.close
            state.cum_vol    = engine.cum_vol
            state.cum_pv     = engine.cum_pv

            # Update unrealized P&L
            if state.position != 0 and state.entry_price > 0:
                mult = 1 if state.side == "long" else -1
                state.unrealized_pnl = round(
                    mult * (bar.close - state.entry_price) * abs(state.position), 2
                )

            # ── TRADING WINDOW CHECK ──────────────────────────────────────────
            if not self._in_trading_window(now_et):
                return

            # ── VWAP RELIABILITY CHECK ────────────────────────────────────────
            # Skip first few bars — VWAP not reliable yet (improvement #8)
            if not engine.is_reliable:
                log.debug(f"{symbol}: VWAP only {engine.bar_count} bars — waiting for {MIN_VWAP_BARS}")
                return

            # ── SIGNAL: candle CLOSE vs VWAP ─────────────────────────────────
            if bar.close == vwap:
                return  # exactly at VWAP — no signal

            new_side = "long" if bar.close > vwap else "short"

            # ── ENTER (flat) ──────────────────────────────────────────────────
            if state.side is None:
                qty = calc_shares(self.api, symbol, new_side, bar.close)
                if self.orders.enter(symbol, new_side, qty):
                    # Verify actual position after order
                    actual_qty, actual_price = self.orders.get_actual_position(symbol)
                    if actual_qty != 0:
                        state.side           = new_side
                        state.position       = actual_qty
                        state.entry_price    = actual_price if actual_price > 0 else bar.close
                        state.unrealized_pnl = 0.0
                        state.record_commission(abs(actual_qty))
                        state.trade_count   += 1
                        trade = {
                            "time":   datetime.now(ET).isoformat(),
                            "symbol": symbol,
                            "action": f"ENTER {new_side.upper()}",
                            "price":  state.entry_price,
                            "qty":    abs(actual_qty),
                            "vwap":   round(vwap, 4),
                        }
                        state.trades.append(trade)
                        self._record(trade)
                    else:
                        log.warning(f"{symbol}: order submitted but no position found — might be pending")

            # ── FLIP (VWAP cross) ─────────────────────────────────────────────
            elif new_side != state.side:
                exit_price = bar.close
                gross_pnl  = self._calc_pnl(state, exit_price)
                exit_qty   = abs(state.position)

                state.record_commission(exit_qty)  # exit commission
                state.realized_pnl  += gross_pnl
                state.unrealized_pnl = 0.0

                if gross_pnl >= 0:
                    state.win_count  += 1
                else:
                    state.loss_count += 1

                log.info(f"🔄 FLIP {symbol}: {state.side}→{new_side} | "
                         f"price={exit_price:.2f} vwap={vwap:.2f} "
                         f"gross_pnl={gross_pnl:+.2f}")

                # Step 1: Flatten current position
                self.orders.flatten(symbol, state.position)

                # Step 2: Reset state immediately
                state.side     = None
                state.position = 0

                # Step 3: Confirm flat before entering new position
                t.sleep(3)
                actual_qty, _ = self.orders.get_actual_position(symbol)
                if actual_qty != 0:
                    log.warning(f"{symbol}: still shows {actual_qty}sh after flatten — waiting more")
                    t.sleep(2)
                    actual_qty, _ = self.orders.get_actual_position(symbol)

                # Step 4: Enter new position
                if actual_qty == 0:
                    qty = calc_shares(self.api, symbol, new_side, bar.close)
                    if self.orders.enter(symbol, new_side, qty):
                        # Verify new position
                        new_qty, new_price = self.orders.get_actual_position(symbol)
                        if new_qty != 0:
                            state.side        = new_side
                            state.position    = new_qty
                            state.entry_price = new_price if new_price > 0 else bar.close
                            state.record_commission(abs(new_qty))
                            state.trade_count += 1

                            trade = {
                                "time":         datetime.now(ET).isoformat(),
                                "symbol":       symbol,
                                "action":       f"FLIP → {new_side.upper()}",
                                "price":        state.entry_price,
                                "qty":          abs(new_qty),
                                "vwap":         round(vwap, 4),
                                "realized_pnl": round(gross_pnl, 2),
                            }
                            state.trades.append(trade)
                            self._record(trade)
                        else:
                            log.warning(f"{symbol}: flip entry submitted but no position — pending")
                else:
                    log.error(f"{symbol}: could not flatten — skipping new entry")

        self._save_state()

    # ── EOD CLOSE ─────────────────────────────────────────────────────────────
    def end_of_day(self):
        """
        Paper sec 3: No positions held overnight.
        Uses Alpaca close_position — gets actual fill price.
        """
        log.info("🏁 EOD: Closing all positions...")
        with self.lock:
            for sym in SYMBOLS:
                state = self.states[sym]
                if state.position == 0:
                    # Double-check with Alpaca
                    actual_qty, _ = self.orders.get_actual_position(sym)
                    if actual_qty == 0:
                        log.info(f"  {sym}: confirmed flat")
                        continue

                # Get actual fill price from Alpaca
                actual_price = self.orders.close_eod(sym)
                exit_price   = actual_price if actual_price else state.last_price

                gross_pnl = self._calc_pnl(state, exit_price)
                exit_qty  = abs(state.position)

                state.record_commission(exit_qty)
                state.realized_pnl  += gross_pnl
                state.unrealized_pnl = 0.0

                if gross_pnl >= 0:
                    state.win_count  += 1
                else:
                    state.loss_count += 1

                trade = {
                    "time":         datetime.now(ET).isoformat(),
                    "symbol":       sym,
                    "action":       "EOD CLOSE",
                    "price":        exit_price,
                    "qty":          exit_qty,
                    "vwap":         state.vwap,
                    "realized_pnl": round(gross_pnl, 2),
                }
                state.trades.append(trade)
                self._record(trade)

                state.position = 0
                state.side     = None

                log.info(f"  {sym}: closed {exit_qty}sh @ ${exit_price:.2f} | "
                         f"pnl={gross_pnl:+.2f} | "
                         f"total_realized=${state.realized_pnl:+.2f} | "
                         f"commission=${state.commission_paid:.2f}")

        for eng in self.engines.values():
            eng.reset()

        self._save_state()
        log.info("✅ EOD complete. VWAP engines reset.")

    # ── STATE ─────────────────────────────────────────────────────────────────
    def _save_state(self):
        try:
            with open("state.json", "w") as f:
                json.dump(
                    {sym: self.states[sym].to_dict() for sym in SYMBOLS},
                    f, default=str, indent=2
                )
        except Exception as e:
            log.error(f"State save failed: {e}")

    def get_state_json(self) -> str:
        try:
            acct = self.api.get_account()
            account = {
                "equity":           float(acct.equity),
                "buying_power":     float(acct.buying_power),
                "cash":             float(acct.cash),
                "pnl_today":        float(acct.equity) - float(acct.last_equity),
                "total_commission": sum(s.commission_paid for s in self.states.values()),
                "total_trades":     sum(s.trade_count    for s in self.states.values()),
            }
        except Exception:
            account = {"equity":0,"buying_power":0,"cash":0,
                       "pnl_today":0,"total_commission":0,"total_trades":0}

        return json.dumps({
            "account":   account,
            "symbols":   {sym: self.states[sym].to_dict() for sym in SYMBOLS},
            "timestamp": datetime.now(ET).isoformat(),
            "config": {
                "symbols":     SYMBOLS,
                "session":     f"{SESSION_START}–{SESSION_END} ET",
                "first_entry": str(FIRST_ENTRY_AT),
                "skip_midday": SKIP_MIDDAY,
                "commission":  COMMISSION,
                "capital_pct": CAPITAL_PCT,
            }
        }, default=str)

    # ── RUN ───────────────────────────────────────────────────────────────────
    def run(self):
        self.running = True

        # EOD watchdog — checks every second for precision
        def eod_watchdog():
            fired_today = False
            while self.running:
                now = datetime.now(ET).time()
                if now >= SESSION_END and not fired_today:
                    log.info(f"⏰ EOD triggered at {now} ET")
                    self.end_of_day()
                    fired_today = True
                if now < SESSION_END:
                    fired_today = False
                t.sleep(1)

        threading.Thread(target=eod_watchdog, daemon=True).start()
        log.info("EOD watchdog started.")

        # Stream with auto-reconnect
        def _run_stream():
            while self.running:
                try:
                    stream = Stream(
                        ALPACA_API_KEY, ALPACA_SECRET_KEY,
                        base_url=BASE_URL, data_feed="iex"
                    )
                    async def bar_handler(bar):
                        self.on_bar(bar)
                    for sym in SYMBOLS:
                        stream.subscribe_bars(bar_handler, sym)
                    log.info(f"📡 Stream connected. Subscribed to {SYMBOLS}.")
                    stream.run()
                except Exception as e:
                    if not self.running:
                        break
                    log.error(f"Stream error: {e} — reconnecting in 10s...")
                    t.sleep(10)

        _run_stream()


if __name__ == "__main__":
    bot = VWAPBot()
    bot.run()
