"""
bot.py — VWAP Trend Trading Bot (FINAL PRODUCTION VERSION)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Paper: Zarattini & Aziz, 2023 (SSRN 4631351)

FINAL IMPROVEMENTS:
[1]  Volume filter — only trade if volume > 50% of session average
[2]  Gap filter — skip if QQQ/TQQQ gaps >1.5% at open (volatile)
[3]  Daily loss limit — stop trading if down 2% on the day
[4]  Buying power guard — never request more than available
[5]  Excel persistence — appends to existing file across all days
[6]  Clean bot exit at 4:05 PM ET after EOD close
[7]  Stream auto-reconnect with exponential backoff
[8]  Position sync on startup
[9]  Stale bar guard (2 min)
[10] Duplicate bar guard
[11] Strict risk — 35% capital per symbol
[12] Commission both sides tracked
[13] Graceful SIGTERM shutdown
[14] Log rotation 5MB x 3
"""

import os, json, signal, threading
import time as t
import logging, logging.handlers
from datetime import datetime, time, date
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

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
SYMBOLS        = ["QQQ", "TQQQ"]
ET             = pytz.timezone("America/New_York")
SESSION_START  = time(9, 30)
FIRST_ENTRY_AT = time(9, 31)
SESSION_END    = time(15, 55)  # close positions 5 min before market close
BOT_EXIT_AT    = time(16, 5)   # exit bot 5 min after market close
COMMISSION     = 0.0005
SKIP_MIDDAY    = False
MIDDAY_START   = time(12, 0)
MIDDAY_END     = time(15, 0)
CAPITAL_PCT    = 0.35          # 35% per symbol — 70% total max
MIN_VWAP_BARS  = 5             # wait 5 bars before VWAP reliable
STALE_BAR_SECS = 120           # ignore bars > 2 min old
ORDER_WAIT     = 2             # wait 2s after order submission
DAILY_LOSS_PCT = 0.02          # stop trading if down 2% today
GAP_FILTER_PCT = 0.015         # skip if gap > 1.5% at open

# ── LOGGING ───────────────────────────────────────────────────────────────────
def _setup_logging():
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh  = logging.handlers.RotatingFileHandler(
        "bot.log", maxBytes=5*1024*1024, backupCount=3)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        root.addHandler(fh)
        root.addHandler(sh)

_setup_logging()
log = logging.getLogger(__name__)


# ── VWAP ENGINE ───────────────────────────────────────────────────────────────
class VWAPEngine:
    """VWAP = Σ(HLC3×Vol)/Σ(Vol) — paper eq.1. Resets every session."""
    def __init__(self):
        self.reset()

    def reset(self):
        self._cum_pv = self._cum_vol = 0.0
        self._bar_count = 0
        self._vol_sum = 0.0  # for volume average

    def update(self, high, low, close, volume) -> float:
        hlc3 = (high + low + close) / 3.0
        self._cum_pv  += hlc3 * volume
        self._cum_vol += volume
        self._vol_sum += volume
        self._bar_count += 1
        return self._cum_pv / self._cum_vol if self._cum_vol else close

    def avg_volume(self) -> float:
        return self._vol_sum / self._bar_count if self._bar_count else 0

    @property
    def is_reliable(self): return self._bar_count >= MIN_VWAP_BARS
    @property
    def bar_count(self):   return self._bar_count
    @property
    def cum_pv(self):      return self._cum_pv
    @property
    def cum_vol(self):     return self._cum_vol


# ── POSITION SIZER ────────────────────────────────────────────────────────────
def calc_shares(api, symbol: str, side: str, price: float) -> int:
    try:
        acct   = api.get_account()
        equity = float(acct.equity)
        bp     = float(acct.buying_power)
    except Exception as e:
        log.error(f"Account fetch failed: {e}")
        return 0

    # Try live quote for better price
    try:
        q   = api.get_latest_quote(symbol)
        ask = float(q.ap) if q.ap and float(q.ap) > 0 else 0
        bid = float(q.bp) if q.bp and float(q.bp) > 0 else 0
        if ask > 0 and bid > 0: price = (ask + bid) / 2.0
        elif ask > 0:            price = ask
        elif bid > 0:            price = bid
    except Exception:
        pass

    if price <= 0:
        log.warning(f"sizing skipped — price=0 for {symbol}")
        return 0

    if side == "long":
        capital = min(equity * CAPITAL_PCT, bp * CAPITAL_PCT)
    else:
        capital = min(equity * 0.25, bp * 0.25)

    if capital < 50:
        log.warning(f"capital too low ${capital:.2f} for {symbol}")
        return 0

    shares = int(capital / price)
    log.info(f"  sizing {symbol} | equity=${equity:.0f} capital=${capital:.0f} price=${price:.2f} → {shares}sh")
    return max(shares, 0)


# ── SYMBOL STATE ──────────────────────────────────────────────────────────────
@dataclass
class SymbolState:
    symbol:          str
    position:        int           = 0
    side:            Optional[str] = None
    entry_price:     float         = 0.0
    vwap:            float         = 0.0
    last_price:      float         = 0.0
    cum_vol:         float         = 0.0
    cum_pv:          float         = 0.0
    unrealized_pnl:  float         = 0.0
    realized_pnl:    float         = 0.0
    commission_paid: float         = 0.0
    trade_count:     int           = 0
    win_count:       int           = 0
    loss_count:      int           = 0
    last_bar_time:   str           = ""
    trades:          list          = field(default_factory=list)

    def to_dict(self):            return asdict(self)
    def record_commission(self, n): self.commission_paid += abs(n) * COMMISSION


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
                side=alpaca_side, type="market", time_in_force="day")
            log.info(f"✅ ENTER {symbol} {alpaca_side.upper()} {qty}sh | id={o.id}")
            t.sleep(ORDER_WAIT)
            return True
        except Exception as e:
            log.error(f"❌ ENTER failed {symbol}: {e}")
            return False

    def flatten(self, symbol: str, position: int) -> bool:
        if position == 0: return True
        side = "sell" if position > 0 else "buy"
        try:
            o = self.api.submit_order(
                symbol=symbol, qty=abs(position),
                side=side, type="market", time_in_force="day")
            log.info(f"🔄 FLATTEN {symbol} {side.upper()} {abs(position)}sh | id={o.id}")
            t.sleep(ORDER_WAIT)
            return True
        except Exception as e:
            log.error(f"❌ FLATTEN failed {symbol}: {e}")
            return False

    def close_eod(self, symbol: str) -> Optional[float]:
        try:
            pos = self.api.get_position(symbol)
            current_price = float(pos.current_price)
        except Exception:
            current_price = None
        try:
            self.api.close_position(symbol)
            log.info(f"🏁 EOD CLOSE {symbol}" + (f" @ ${current_price:.2f}" if current_price else ""))
            return current_price
        except tradeapi.rest.APIError as e:
            if "position does not exist" in str(e).lower() or "no position" in str(e).lower():
                log.info(f"  {symbol}: already flat")
            else:
                log.error(f"❌ EOD close failed {symbol}: {e}")
            return None
        except Exception as e:
            log.error(f"❌ EOD close failed {symbol}: {e}")
            return None

    def get_actual_position(self, symbol: str) -> tuple:
        try:
            pos = self.api.get_position(symbol)
            return int(pos.qty), float(pos.avg_entry_price)
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

        # Daily tracking
        self.start_equity    = 0.0
        self.daily_loss_halt = False
        self.gap_checked     = False
        self.gap_ok          = {s: True for s in SYMBOLS}

        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT,  self._shutdown)

        log.info(f"VWAPBot FINAL | symbols={SYMBOLS} | commission=${COMMISSION}/sh")
        log.info(f"Session: {SESSION_START}–{SESSION_END} ET | Capital/symbol: {CAPITAL_PCT*100:.0f}%")
        log.info(f"Daily loss limit: {DAILY_LOSS_PCT*100:.0f}% | Gap filter: {GAP_FILTER_PCT*100:.1f}%")

        self._sync_startup()

    def _shutdown(self, signum, frame):
        log.info(f"🛑 Shutdown signal — closing positions")
        self.end_of_day()
        self.running = False

    def _sync_startup(self):
        try:
            acct = self.api.get_account()
            self.start_equity = float(acct.equity)
            log.info(f"Start equity: ${self.start_equity:,.2f}")
        except Exception:
            pass

        for sym in SYMBOLS:
            qty, avg_price = self.orders.get_actual_position(sym)
            if qty != 0:
                s = self.states[sym]
                s.position    = qty
                s.side        = "long" if qty > 0 else "short"
                s.entry_price = avg_price
                log.info(f"  Startup: {sym} {s.side} {abs(qty)}sh @ ${avg_price:.2f}")
            else:
                log.info(f"  Startup: {sym} flat")

    # ── HELPERS ───────────────────────────────────────────────────────────────
    def _equity(self) -> float:
        try:    return float(self.api.get_account().equity)
        except: return self.start_equity

    def _total_realized(self) -> float:
        return sum(s.realized_pnl for s in self.states.values())

    def _calc_pnl(self, state: SymbolState, exit_price: float) -> float:
        qty = abs(state.position)
        return (exit_price - state.entry_price)*qty if state.side=="long" \
               else (state.entry_price - exit_price)*qty

    def _record(self, trade: dict):
        equity  = self._equity()
        cum_pnl = self._total_realized()
        threading.Thread(target=log_trade_to_excel,
                         args=(trade, equity, cum_pnl), daemon=True).start()

    def _in_window(self, now: time) -> bool:
        if now < FIRST_ENTRY_AT or now >= SESSION_END: return False
        if SKIP_MIDDAY and MIDDAY_START <= now < MIDDAY_END: return False
        return True

    def _is_stale(self, ts) -> bool:
        try:
            bar_ts = ts.timestamp() if hasattr(ts,'timestamp') else \
                     datetime.fromisoformat(str(ts).replace("Z","+00:00")).timestamp()
            return (t.time() - bar_ts) > STALE_BAR_SECS
        except: return False

    def _check_daily_loss(self) -> bool:
        if self.daily_loss_halt: return True
        if self.start_equity <= 0: return False
        equity = self._equity()
        loss_pct = (self.start_equity - equity) / self.start_equity
        if loss_pct >= DAILY_LOSS_PCT:
            log.warning(f"🛑 Daily loss limit hit: {loss_pct*100:.1f}% — halting trades")
            self.daily_loss_halt = True
            return True
        return False

    def _check_gap(self, symbol: str, bar_open: float, bar_close: float) -> bool:
        """Skip if opening gap > GAP_FILTER_PCT — volatile day."""
        if self.gap_checked: return self.gap_ok[symbol]
        try:
            bars = self.api.get_bars(symbol, "1Day", limit=2).df
            if len(bars) >= 2:
                prev_close = float(bars.iloc[-2]['close'])
                gap = abs(bar_open - prev_close) / prev_close
                if gap > GAP_FILTER_PCT:
                    log.warning(f"⚠️ {symbol} gap {gap*100:.1f}% > {GAP_FILTER_PCT*100:.1f}% — skipping today")
                    self.gap_ok[symbol] = False
                else:
                    log.info(f"✅ {symbol} gap {gap*100:.2f}% OK")
                    self.gap_ok[symbol] = True
        except Exception as e:
            log.warning(f"Gap check failed {symbol}: {e} — trading allowed")
            self.gap_ok[symbol] = True
        if all(v is not None for v in self.gap_ok.values()):
            self.gap_checked = True
        return self.gap_ok[symbol]

    # ── BAR HANDLER ───────────────────────────────────────────────────────────
    def on_bar(self, bar):
        symbol = bar.symbol
        if symbol not in SYMBOLS: return
        if self._is_stale(bar.timestamp): return

        now_et  = datetime.now(ET).time()
        bar_key = str(bar.timestamp)

        with self.lock:
            state  = self.states[symbol]
            engine = self.engines[symbol]

            if bar_key == state.last_bar_time: return
            state.last_bar_time = bar_key

            # Reset at session open
            if now_et <= SESSION_START:
                engine.reset()
                state.side     = None
                state.position = 0
                self.daily_loss_halt = False
                self.gap_checked     = False
                self.gap_ok          = {s: True for s in SYMBOLS}
                try:
                    acct = self.api.get_account()
                    self.start_equity = float(acct.equity)
                except: pass
                return

            if now_et >= SESSION_END: return

            # Update VWAP
            vwap             = engine.update(bar.high, bar.low, bar.close, bar.volume)
            state.vwap       = round(vwap, 4)
            state.last_price = bar.close
            state.cum_vol    = engine.cum_vol
            state.cum_pv     = engine.cum_pv

            # Unrealized P&L
            if state.position != 0 and state.entry_price > 0:
                mult = 1 if state.side == "long" else -1
                state.unrealized_pnl = round(
                    mult * (bar.close - state.entry_price) * abs(state.position), 2)

            if not self._in_window(now_et): return
            if not engine.is_reliable: return
            if self._check_daily_loss(): return

            # Gap filter on first candle
            if engine.bar_count == FIRST_ENTRY_AT.minute - SESSION_START.minute:
                if not self._check_gap(symbol, bar.open if hasattr(bar,'open') else bar.close, bar.close):
                    return

            # Volume filter — skip if volume < 50% of session average
            avg_vol = engine.avg_volume()
            if avg_vol > 0 and bar.volume < avg_vol * 0.5:
                log.debug(f"{symbol} low volume {bar.volume:.0f} < {avg_vol*0.5:.0f} avg — skipping")
                return

            # Signal
            if bar.close == vwap: return
            new_side = "long" if bar.close > vwap else "short"

            # ── ENTER ─────────────────────────────────────────────────────────
            if state.side is None:
                qty = calc_shares(self.api, symbol, new_side, bar.close)
                if self.orders.enter(symbol, new_side, qty):
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

            # ── FLIP ──────────────────────────────────────────────────────────
            elif new_side != state.side:
                exit_price = bar.close
                gross_pnl  = self._calc_pnl(state, exit_price)
                exit_qty   = abs(state.position)

                state.record_commission(exit_qty)
                state.realized_pnl  += gross_pnl
                state.unrealized_pnl = 0.0
                if gross_pnl >= 0: state.win_count  += 1
                else:              state.loss_count += 1

                log.info(f"🔄 FLIP {symbol}: {state.side}→{new_side} | "
                         f"price={exit_price:.2f} vwap={vwap:.2f} pnl={gross_pnl:+.2f}")

                self.orders.flatten(symbol, state.position)
                state.side = None
                state.position = 0

                t.sleep(3)

                actual_qty, _ = self.orders.get_actual_position(symbol)
                if actual_qty != 0:
                    log.warning(f"{symbol}: still {actual_qty}sh after flatten — waiting")
                    t.sleep(2)
                    actual_qty, _ = self.orders.get_actual_position(symbol)

                if actual_qty == 0:
                    qty = calc_shares(self.api, symbol, new_side, bar.close)
                    if self.orders.enter(symbol, new_side, qty):
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
                    log.error(f"{symbol}: could not flatten — skipping entry")

        self._save_state()

    # ── EOD ───────────────────────────────────────────────────────────────────
    def end_of_day(self):
        log.info("🏁 EOD: Closing all positions...")
        with self.lock:
            for sym in SYMBOLS:
                state = self.states[sym]
                if state.position == 0:
                    actual_qty, _ = self.orders.get_actual_position(sym)
                    if actual_qty == 0:
                        log.info(f"  {sym}: confirmed flat")
                        continue

                actual_price = self.orders.close_eod(sym)
                exit_price   = actual_price if actual_price else state.last_price
                gross_pnl    = self._calc_pnl(state, exit_price)
                exit_qty     = abs(state.position)

                state.record_commission(exit_qty)
                state.realized_pnl  += gross_pnl
                state.unrealized_pnl = 0.0
                if gross_pnl >= 0: state.win_count  += 1
                else:              state.loss_count += 1

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
                         f"pnl={gross_pnl:+.2f} | total=${state.realized_pnl:+.2f} | "
                         f"commission=${state.commission_paid:.2f}")

        for eng in self.engines.values():
            eng.reset()
        self._save_state()
        log.info("✅ EOD complete.")

    # ── STATE ─────────────────────────────────────────────────────────────────
    def _save_state(self):
        try:
            with open("state.json", "w") as f:
                json.dump({s: self.states[s].to_dict() for s in SYMBOLS},
                          f, default=str, indent=2)
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
            "symbols":   {s: self.states[s].to_dict() for s in SYMBOLS},
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

        def eod_watchdog():
            fired_today = False
            while self.running:
                now = datetime.now(ET).time()
                if now >= SESSION_END and not fired_today:
                    log.info(f"⏰ EOD triggered at {now} ET")
                    self.end_of_day()
                    fired_today = True
                if now >= BOT_EXIT_AT and fired_today:
                    log.info(f"🏁 Bot exiting at {now} ET")
                    self.running = False
                    break
                if now < SESSION_END:
                    fired_today = False
                t.sleep(1)

        threading.Thread(target=eod_watchdog, daemon=True).start()
        log.info("EOD watchdog started — closes 15:55, exits 16:05 ET")

        def _run_stream():
            backoff = 5
            while self.running:
                try:
                    stream = Stream(ALPACA_API_KEY, ALPACA_SECRET_KEY,
                                    base_url=BASE_URL, data_feed="iex")
                    async def bar_handler(bar): self.on_bar(bar)
                    for sym in SYMBOLS: stream.subscribe_bars(bar_handler, sym)
                    log.info(f"📡 Stream connected. Subscribed to {SYMBOLS}.")
                    backoff = 5
                    stream.run()
                except Exception as e:
                    if not self.running: break
                    log.error(f"Stream error: {e} — reconnecting in {backoff}s")
                    t.sleep(backoff)
                    backoff = min(backoff * 2, 60)

        _run_stream()


if __name__ == "__main__":
    bot = VWAPBot()
    bot.run()
