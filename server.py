"""
Flask WebSocket + REST API server
Serves bot state to the React dashboard in real-time
"""

from flask import Flask, jsonify
from flask_cors import CORS
from flask_sock import Sock
import threading
import time
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)
sock = Sock(app)

# ── GLOBALS ───────────────────────────────────────────────────────────────────
_bot        = None
_ws_clients = set()
_ws_lock    = threading.Lock()


def set_bot(bot):
    global _bot
    _bot = bot


# ── BROADCAST LOOP ────────────────────────────────────────────────────────────
def broadcast_loop():
    """Push bot state to all connected WebSocket clients every second."""
    global _bot, _ws_clients, _ws_lock
    while True:
        if _bot:
            try:
                payload = _bot.get_state_json()
                dead = set()
                with _ws_lock:
                    clients = set(_ws_clients)
                for ws in clients:
                    try:
                        ws.send(payload)
                    except Exception:
                        dead.add(ws)
                with _ws_lock:
                    _ws_clients -= dead
            except Exception as e:
                log.error(f"Broadcast error: {e}")
        time.sleep(1)


threading.Thread(target=broadcast_loop, daemon=True).start()


# ── REST ENDPOINTS ────────────────────────────────────────────────────────────
@app.route("/api/state")
def get_state():
    global _bot
    if not _bot:
        return jsonify({"error": "Bot not running"}), 503
    return _bot.get_state_json(), 200, {"Content-Type": "application/json"}


@app.route("/api/trades/<symbol>")
def get_trades(symbol):
    global _bot
    if not _bot or symbol not in _bot.states:
        return jsonify([])
    return jsonify(_bot.states[symbol].trades[-50:])


@app.route("/api/status")
def status():
    global _bot
    return jsonify({
        "running": _bot is not None and _bot.running,
        "symbols": ["QQQ", "TQQQ"]
    })


# ── WEBSOCKET ─────────────────────────────────────────────────────────────────
@sock.route("/ws")
def ws_handler(ws):
    global _ws_clients, _ws_lock
    with _ws_lock:
        _ws_clients.add(ws)
    log.info(f"WS client connected. Total: {len(_ws_clients)}")
    try:
        while True:
            msg = ws.receive(timeout=30)
            if msg is None:
                break
    except Exception:
        pass
    finally:
        with _ws_lock:
            _ws_clients.discard(ws)
        log.info(f"WS client disconnected. Total: {len(_ws_clients)}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
