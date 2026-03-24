"""
main.py — Launch bot + Flask server together
Usage: python main.py
"""

import threading
from bot import VWAPBot
import server

def run_server(bot):
    server.set_bot(bot)
    server.app.run(host="0.0.0.0", port=5050, debug=False, use_reloader=False)

if __name__ == "__main__":
    bot = VWAPBot()

    # Start Flask server in background
    t = threading.Thread(target=run_server, args=(bot,), daemon=True)
    t.start()

    print("=" * 60)
    print("  VWAP Trading Bot — QQQ + TQQQ")
    print("  Dashboard API: http://localhost:5050")
    print("  WebSocket:     ws://localhost:5050/ws")
    print("=" * 60)
    # Run bot (blocking — streams live 1-min bars)
    bot.run()

    # Run bot (blocking — streams live 1-min bars)
    bot.run()
