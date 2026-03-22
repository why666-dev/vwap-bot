"""
alerts.py — Compatibility shim (Telegram removed)
All logging now goes to Excel via excel_logger.py
"""
from excel_logger import log_trade_to_excel

def send_alert(*args, **kwargs):
    pass  # Telegram removed

def send_eod_summary(*args, **kwargs):
    pass  # Telegram removed
