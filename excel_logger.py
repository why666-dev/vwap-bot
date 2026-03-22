"""
excel_logger.py — Excel Trade Logger for VWAP Bot
No Telegram — pure Excel logging only.

IMPROVEMENTS:
[1] Separate sheets per symbol (QQQ / TQQQ / All Trades)
[2] Auto-creates workbook if missing or corrupted
[3] Thread-safe with lock
[4] Correct column mapping — no formula errors
[5] Performance summary with live Excel formulas
[6] Daily P&L tracking sheet
[7] Robust date/time parsing
"""

import os
import threading
import logging
from datetime import datetime
import pytz

log         = logging.getLogger(__name__)
ET          = pytz.timezone("America/New_York")
EXCEL_PATH  = "trades_log.xlsx"
COMMISSION  = 0.0005
_lock       = threading.Lock()

# ── COLOR PALETTE ─────────────────────────────────────────────────────────────
C = {
    "card":     "0D1117", "alt":      "161B22",
    "border":   "21262D", "green":    "00FF88",
    "red":      "FF4466", "yellow":   "F7C948",
    "purple":   "8B8BFF", "blue":     "4D9EFF",
    "text":     "E6EDF3", "dim":      "8B949E",
    "long_bg":  "0A2E1A", "short_bg": "2E0A0A",
    "flip_bg":  "2E2A0A", "eod_bg":   "0A0A2E",
}

# Column definitions
HEADERS = [
    "Date","Time (ET)","Symbol","Action","Side",
    "Price ($)","VWAP ($)","Qty",
    "Notional ($)","Gross P&L ($)","Commission ($)",
    "Net P&L ($)","Cumul. P&L ($)","Equity ($)","Notes"
]
WIDTHS  = [12,10,8,18,8,12,12,9,13,13,13,12,14,14,22]


def _mk_wb():
    """Create a fresh workbook with all sheets."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side as XS
    from openpyxl.utils import get_column_letter

    wb   = Workbook()
    thin = XS(style="thin", color=C["border"])
    bdr  = lambda: Border(left=thin, right=thin, top=thin, bottom=thin)

    def fn(col, bold=False, sz=9):
        return Font(name="Consolas", bold=bold, size=sz, color=col)
    def fp(col):
        return PatternFill("solid", fgColor=col)
    def fa(h="center", wrap=False):
        return Alignment(horizontal=h, vertical="center", wrap_text=wrap)

    def make_trades_sheet(ws, tab_color, accent):
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = tab_color

        # Title
        span = get_column_letter(len(HEADERS))
        ws.merge_cells(f"A1:{span}1")
        ws["A1"].value     = f"⚡  VWAP BOT — {ws.title.upper()} TRADE LOG"
        ws["A1"].font      = fn(accent, True, 13)
        ws["A1"].fill      = fp(C["card"])
        ws["A1"].alignment = fa()
        ws.row_dimensions[1].height = 34

        # Subtitle
        ws.merge_cells(f"A2:{span}2")
        ws["A2"].value = "Strategy: 1-min VWAP cross | Long>VWAP | Short<VWAP | Flip on candle close | EOD 15:58 ET | Alpaca Paper"
        ws["A2"].font      = Font(name="Consolas", size=8, italic=True, color=C["dim"])
        ws["A2"].fill      = fp("0A0F16")
        ws["A2"].alignment = fa()
        ws.row_dimensions[2].height = 16

        # Column headers
        for i, (h, w) in enumerate(zip(HEADERS, WIDTHS), 1):
            c = ws.cell(row=3, column=i)
            c.value     = h
            c.font      = fn(accent, True, 9)
            c.fill      = fp(C["alt"])
            c.alignment = fa(wrap=True)
            c.border    = bdr()
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.row_dimensions[3].height = 28
        ws.freeze_panes = "A4"

    # ── ALL TRADES ────────────────────────────────────────────────────────────
    ws_all = wb.active
    ws_all.title = "All Trades"
    make_trades_sheet(ws_all, C["green"], C["green"])

    # ── QQQ SHEET ─────────────────────────────────────────────────────────────
    ws_qqq = wb.create_sheet("QQQ")
    make_trades_sheet(ws_qqq, "4D9EFF", C["blue"])

    # ── TQQQ SHEET ────────────────────────────────────────────────────────────
    ws_tqqq = wb.create_sheet("TQQQ")
    make_trades_sheet(ws_tqqq, C["purple"], C["purple"])

    # ── PERFORMANCE SUMMARY ───────────────────────────────────────────────────
    ws_p = wb.create_sheet("Performance")
    ws_p.sheet_view.showGridLines = False
    ws_p.sheet_properties.tabColor = C["yellow"]

    ws_p.merge_cells("A1:G1")
    ws_p["A1"].value     = "⚡  VWAP BOT — PERFORMANCE SUMMARY (Live Excel Formulas)"
    ws_p["A1"].font      = fn(C["yellow"], True, 13)
    ws_p["A1"].fill      = fp(C["card"])
    ws_p["A1"].alignment = fa()
    ws_p.row_dimensions[1].height = 34

    ph = [("Metric",30),("QQQ",18),("TQQQ",18),("Combined",18),("Paper Target",18),("Notes",30)]
    for i,(h,w) in enumerate(ph,1):
        c = ws_p.cell(row=2,column=i)
        c.value=h; c.font=fn(C["yellow"],True,9); c.fill=fp(C["alt"])
        c.alignment=fa(wrap=True); c.border=bdr()
        ws_p.column_dimensions[get_column_letter(i)].width=w
    ws_p.row_dimensions[2].height=26

    # All formulas reference All Trades sheet column L (Net P&L)
    perf_rows = [
        ("Total Net P&L ($)",
         '=SUMIFS(\'All Trades\'!L:L,\'All Trades\'!C:C,"QQQ")',
         '=SUMIFS(\'All Trades\'!L:L,\'All Trades\'!C:C,"TQQQ")',
         "=B3+C3","—","After $0.0005/sh commission"),
        ("Total Commission ($)",
         '=SUMIFS(\'All Trades\'!K:K,\'All Trades\'!C:C,"QQQ")',
         '=SUMIFS(\'All Trades\'!K:K,\'All Trades\'!C:C,"TQQQ")',
         "=B4+C4","—","At $0.0005/share each side"),
        ("Total Trades",
         '=COUNTIF(\'All Trades\'!C:C,"QQQ")',
         '=COUNTIF(\'All Trades\'!C:C,"TQQQ")',
         "=B5+C5","~22,000/yr","Zarattini Table 3"),
        ("Winning Trades",
         '=COUNTIFS(\'All Trades\'!C:C,"QQQ",\'All Trades\'!L:L,">"&0)',
         '=COUNTIFS(\'All Trades\'!C:C,"TQQQ",\'All Trades\'!L:L,">"&0)',
         "=B6+C6","—","Net P&L > 0"),
        ("Losing Trades",
         '=COUNTIFS(\'All Trades\'!C:C,"QQQ",\'All Trades\'!L:L,"<"&0)',
         '=COUNTIFS(\'All Trades\'!C:C,"TQQQ",\'All Trades\'!L:L,"<"&0)',
         "=B7+C7","—","Net P&L < 0"),
        ("Hit Ratio",
         "=IFERROR(B6/B5,0)","=IFERROR(C6/C5,0)",
         "=IFERROR((B6+C6)/(B5+C5),0)","~17%",
         "Low hit ratio is normal for trend strategies"),
        ("Avg Win ($)",
         '=IFERROR(AVERAGEIFS(\'All Trades\'!L:L,\'All Trades\'!C:C,"QQQ",\'All Trades\'!L:L,">"&0),0)',
         '=IFERROR(AVERAGEIFS(\'All Trades\'!L:L,\'All Trades\'!C:C,"TQQQ",\'All Trades\'!L:L,">"&0),0)',
         "=IFERROR((B3+C3)/(B6+C6),0)","—",""),
        ("Avg Loss ($)",
         '=IFERROR(AVERAGEIFS(\'All Trades\'!L:L,\'All Trades\'!C:C,"QQQ",\'All Trades\'!L:L,"<"&0),0)',
         '=IFERROR(AVERAGEIFS(\'All Trades\'!L:L,\'All Trades\'!C:C,"TQQQ",\'All Trades\'!L:L,"<"&0),0)',
         "=IFERROR((B3+C3)/(B7+C7),0)","—",""),
        ("Gain:Loss Ratio",
         "=IFERROR(ABS(B8/B9),0)","=IFERROR(ABS(C8/C9),0)",
         "=IFERROR(ABS(D8/D9),0)","~5.7x","Zarattini Table 4"),
        ("Max Single Win ($)",
         '=IFERROR(MAXIFS(\'All Trades\'!L:L,\'All Trades\'!C:C,"QQQ"),0)',
         '=IFERROR(MAXIFS(\'All Trades\'!L:L,\'All Trades\'!C:C,"TQQQ"),0)',
         "=MAX(B11,C11)","6.5% QQQ / 20.9% TQQQ","Table 4"),
        ("Max Single Loss ($)",
         '=IFERROR(MINIFS(\'All Trades\'!L:L,\'All Trades\'!C:C,"QQQ"),0)',
         '=IFERROR(MINIFS(\'All Trades\'!L:L,\'All Trades\'!C:C,"TQQQ"),0)',
         "=MIN(B12,C12)","-1.4% QQQ / -4.1% TQQQ","Table 4"),
        ("Latest Equity ($)",
         "=IFERROR(INDEX('All Trades'!N:N,MATCH(9.9E+307,'All Trades'!N:N,1)),0)",
         "=B13","=B13","—","Last recorded equity"),
    ]

    for r, row in enumerate(perf_rows, 3):
        for ci, val in enumerate(row, 1):
            c = ws_p.cell(row=r, column=ci, value=val)
            c.fill      = fp(C["alt"] if r%2==0 else C["card"])
            c.alignment = fa() if ci>1 else Alignment(horizontal="left", vertical="center")
            c.border    = bdr()
            c.font      = fn(C["text"], ci==1, 9)
        ws_p.row_dimensions[r].height = 22

    # Format hit ratio row as percentage
    for col in (2,3,4):
        ws_p.cell(row=8,column=col).number_format = "0.0%"

    wb.save(EXCEL_PATH)
    log.info(f"✅ Excel workbook created: {EXCEL_PATH}")


def _ensure_wb():
    """Load workbook or create fresh if missing/corrupt."""
    from openpyxl import load_workbook

    if not os.path.exists(EXCEL_PATH):
        _mk_wb()
        return load_workbook(EXCEL_PATH)

    try:
        wb = load_workbook(EXCEL_PATH)
        # Verify required sheets exist
        for sname in ("All Trades","QQQ","TQQQ","Performance"):
            if sname not in wb.sheetnames:
                raise ValueError(f"Missing sheet: {sname}")
        return wb
    except Exception as e:
        log.warning(f"Excel corrupt/incomplete ({e}) — recreating")
        try:
            os.remove(EXCEL_PATH)
        except Exception:
            pass
        _mk_wb()
        return load_workbook(EXCEL_PATH)


def log_trade_to_excel(trade: dict, account_equity: float, cum_net_pnl: float):
    """Thread-safe trade row append. Never raises."""
    with _lock:
        try:
            _write_row(trade, account_equity, cum_net_pnl)
        except Exception as e:
            log.error(f"Excel write failed: {e}")


def _write_row(trade: dict, equity: float, cum_net_pnl: float):
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side as XS

    wb = _ensure_wb()

    action    = str(trade.get("action", ""))
    symbol    = str(trade.get("symbol", ""))
    price     = float(trade.get("price",  0) or 0)
    vwap_val  = float(trade.get("vwap",   0) or 0)
    qty       = int(trade.get("qty",      0) or 0)
    gross_pnl = trade.get("realized_pnl")
    ts_raw    = trade.get("time", "")

    # Parse timestamp
    try:
        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ET.localize(ts)
    except Exception:
        ts = datetime.now(ET)

    date_str = ts.strftime("%Y-%m-%d")
    time_str = ts.strftime("%H:%M:%S")

    side     = ("LONG"  if "LONG"  in action else
                "SHORT" if "SHORT" in action else "—")
    notional = round(price * qty, 2)
    comm     = round(qty * COMMISSION, 2)
    gross_v  = float(gross_pnl) if gross_pnl is not None else None
    net_pnl  = round(gross_v - comm, 2) if gross_v is not None else None

    # Row color
    if   "ENTER" in action and "LONG"  in action: bg = C["long_bg"]
    elif "ENTER" in action and "SHORT" in action: bg = C["short_bg"]
    elif "FLIP"  in action:                        bg = C["flip_bg"]
    elif "EOD"   in action:                        bg = C["eod_bg"]
    else:                                           bg = C["card"]

    thin   = XS(style="thin", color=C["border"])
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ACTION_COLORS = {
        "ENTER LONG":   C["green"],  "ENTER SHORT":  C["red"],
        "FLIP → LONG":  C["yellow"], "FLIP → SHORT": C["yellow"],
        "EOD CLOSE":    C["purple"],
    }

    row_vals = [
        date_str, time_str, symbol, action, side,
        price, vwap_val, qty,
        notional,
        gross_v  if gross_v  is not None else "",
        comm,
        net_pnl  if net_pnl  is not None else "",
        round(cum_net_pnl, 2),
        round(equity, 2),
        ""  # Notes
    ]

    # Write to All Trades + symbol-specific sheet
    target_sheets = ["All Trades"]
    if symbol in ("QQQ", "TQQQ"):
        target_sheets.append(symbol)

    for sname in target_sheets:
        if sname not in wb.sheetnames:
            continue
        ws       = wb[sname]
        next_row = max(ws.max_row + 1, 4)

        for ci, val in enumerate(row_vals, 1):
            cell = ws.cell(row=next_row, column=ci, value=val)
            cell.fill      = PatternFill("solid", fgColor=bg)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border    = border

            # P&L columns — color by sign
            if ci in (10, 12, 13) and val != "":
                try:
                    col = C["green"] if float(val) >= 0 else C["red"]
                    cell.font = Font(name="Consolas", size=9, bold=True, color=col)
                except Exception:
                    cell.font = Font(name="Consolas", size=9, color=C["text"])
            elif ci == 4:  # Action
                col = ACTION_COLORS.get(action, C["text"])
                cell.font = Font(name="Consolas", size=9, bold=True, color=col)
            elif ci == 5:  # Side
                col = (C["green"] if val=="LONG" else C["red"] if val=="SHORT" else C["dim"])
                cell.font = Font(name="Consolas", size=9, bold=True, color=col)
            elif ci == 15:  # Notes
                cell.font = Font(name="Consolas", size=8, italic=True, color=C["dim"])
                cell.alignment = Alignment(horizontal="left", vertical="center")
            else:
                cell.font = Font(name="Consolas", size=9, color=C["text"])

        ws.row_dimensions[next_row].height = 20

    wb.save(EXCEL_PATH)
    log.info(f"Excel ✅ {symbol} {action} | net=${net_pnl} | equity=${equity:.0f}")
