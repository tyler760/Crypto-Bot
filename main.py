# main.py
import json
import os
import re
import sys
import time
import uuid
import sqlite3
import logging
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from binance.spot import Spot  # Official binance-connector (works with Binance.US)

# =========================
# Flask app & stdout logging
# =========================
app = Flask(__name__)

# Stream logs to stdout so Render captures them regardless of the UI
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
root = logging.getLogger()
root.handlers.clear()
root.addHandler(handler)
root.setLevel(logging.INFO)  # set to DEBUG for extra detail

logger = logging.getLogger("main")

# =========================
# Binance.US client
# =========================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
client = Spot(api_key=API_KEY, api_secret=API_SECRET, base_url="https://api.binance.us")

# =========================
# SQLite setup
# =========================
DB_FILE = "trades.db"

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        # Trades table (your existing schema)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            symbol TEXT,
            qty REAL,
            entry_price REAL,
            sl_price REAL,
            tp_price REAL,
            timestamp TEXT,
            status TEXT,
            error TEXT,
            client_id TEXT,
            note TEXT
        )
        """)
        # Webhook hit log (new) — stores headers/raw/parsed JSON and status
        conn.execute("""
        CREATE TABLE IF NOT EXISTS webhook_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            path TEXT,
            headers TEXT,
            raw TEXT,
            json TEXT,
            status_code INTEGER
        )
        """)
init_db()

def log_trade(row: dict):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """INSERT INTO trades 
               (action, symbol, qty, entry_price, sl_price, tp_price, timestamp, status, error, client_id, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row.get("action"),
                row.get("symbol"),
                row.get("qty"),
                row.get("entry_price"),
                row.get("sl_price"),
                row.get("tp_price"),
                row.get("timestamp") or datetime.utcnow().isoformat(),
                row.get("status"),
                row.get("error") or "",
                row.get("client_id") or "",
                row.get("note") or ""
            )
        )

def log_webhook_hit(path: str, headers: dict, raw: str, as_json, status_code: int):
    """Persist every webhook call so you can audit deliveries even without the Render log UI."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "INSERT INTO webhook_log (ts, path, headers, raw, json, status_code) VALUES (?, ?, ?, ?, ?, ?)",
            (datetime.utcnow().isoformat(), path, str(headers), raw, str(as_json), int(status_code))
        )

# Helpers
ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT"}  # Symbols you allow

# Fallback sizes used only if the webhook payload does NOT include "qty"
DEFAULT_QTY = {
    "BTCUSDT": 0.001,   # updated per your request
    "ETHUSDT": 0.02
}

def normalize_symbol(raw_symbol: str) -> str:
    """
    Accepts TradingView formats like 'BINANCE:BTCUSDT', 'BTC/USDT', or 'BTCUSDT'
    and returns a clean Binance.US symbol.
    """
    if not raw_symbol:
        return ""
    s = raw_symbol.strip().upper()
    if ":" in s:
        s = s.split(":")[-1].strip()
    s = s.replace("/", "")
    # Map USD -> USDT if allowed
    if s not in ALLOWED_SYMBOLS and s.endswith("USD"):
        maybe = s[:-3] + "USDT"
        if maybe in ALLOWED_SYMBOLS:
            s = maybe
    return s

def place_market_order_with_fallback(side: str, symbol: str, qty: float):
    """
    Place a MARKET order. If Binance.US returns a non-JSON body (HTML/404)
    even though the order fills, recover by fetching via newClientOrderId.
    """
    client_id = f"tv_{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}"

    try:
        order = client.new_order(
            symbol=symbol,
            side=side,              # "BUY" or "SELL"
            type="MARKET",
            quantity=qty,
            newClientOrderId=client_id
        )
        return {"ok": True, "order": order, "client_id": client_id, "note": ""}

    except Exception as e:
        msg = str(e)
        app.logger.error("Primary order error: %s", msg)

        # Binance.US sometimes returns HTML/invalid JSON while order actually filled.
        if "Invalid JSON error message" in msg or "404 Not found" in msg or "code=0" in msg:
            try:
                status = client.get_order(symbol=symbol, origClientOrderId=client_id)
                return {"ok": True, "order": status, "client_id": client_id, "note": "Recovered after parse error"}
            except Exception as e2:
                return {"ok": False, "error": f"Fallback lookup failed: {e2}", "client_id": client_id}
        else:
            return {"ok": False, "error": msg, "client_id": client_id}

# =========================
# Routes
# =========================
@app.route("/", methods=["GET"])
def home():
    return "Bot is running (Binance.US)"

@app.route("/health", methods=["GET"])
def health():
    k = os.getenv("BINANCE_API_KEY", "")
    s = os.getenv("BINANCE_API_SECRET", "")
    def mask(v): 
        return v and (v[:3] + "…" + v[-3:]) or "MISSING"
    return {"ok": True, "api_key": mask(k), "api_secret": mask(s)}

@app.route("/dashboard", methods=["GET"])
def dashboard():
    with sqlite3.connect(DB_FILE) as conn:
        trades = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 50").fetchall()
    return render_template("dashboard.html", trades=trades)

@app.route("/logs", methods=["GET"])
def logs():
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute(
            "SELECT timestamp, action, symbol, status, error, client_id FROM trades WHERE status='error' ORDER BY id DESC LIMIT 100"
        ).fetchall()
    return render_template("logs.html", logs=rows)

@app.route("/webhook-log")
def webhook_log():
    """Simple viewer to see recent webhook deliveries (headers/raw/parsed)."""
    limit = int(request.args.get("limit", 50))
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute(
            "SELECT ts, path, status_code, substr(headers,1,200), substr(raw,1,200), substr(json,1,200) "
            "FROM webhook_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    html = ["<h2>Recent Webhook Hits</h2><table border='1' cellpadding='4'>"]
    html.append("<tr><th>ts (UTC)</th><th>path</th><th>status</th><th>headers</th><th>raw</th><th>json</th></tr>")
    for ts, path, sc, hdr, raw, js in rows:
        html.append(f"<tr><td>{ts}</td><td>{path}</td><td>{sc}</td><td><pre>{hdr}</pre></td><td><pre>{raw}</pre></td><td><pre>{js}</pre></td></tr>")
    html.append("</table>")
    return "".join(html)

# --- Single handler mounted at both /webhook and /tv ---
def _handle_tv():
    try:
        # ---- Capture request data up-front (so we can persist regardless of parse result) ----
        hdrs = dict(request.headers)
        raw_data = request.get_data(as_text=True)  # cache=True by default, JSON parse still works

        # ---- Tolerant JSON parse (handles plain text gracefully) ----
        data = request.get_json(silent=True)
        if data is None:
            try:
                if raw_data and raw_data.strip().startswith("{"):
                    data = json.loads(raw_data)
                else:
                    data = {}
            except Exception:
                data = {}

        # ---- Diagnostics logging ----
        app.logger.warning("TV HEADERS: %s", hdrs)
        app.logger.warning("TV RAW: %s", raw_data)
        app.logger.warning("TV JSON: %s", data)

        # If still empty/non-JSON: acknowledge and ignore
        if not data:
            log_webhook_hit(request.path, hdrs, raw_data, {"note": "non-json or empty; acked"}, 200)
            return jsonify({"ok": True, "note": "non-json or empty; ignored"}), 200

        # ---- Non-trade diagnostics (ping / debug) ----
        if data.get("ping") is True:
            app.logger.info("PING ok: %s", data)
            log_webhook_hit(request.path, hdrs, raw_data, data, 200)
            return jsonify({"ok": True, "note": "pong"}), 200

        if "debug" in data:
            app.logger.info("DEBUG msg: %s", data)
            log_webhook_hit(request.path, hdrs, raw_data, data, 200)
            return jsonify({"ok": True, "note": "debug received"}), 200

        # ---- Actionable trades only below ----
        action = str(data.get("action", "")).upper()
        raw_symbol = str(data.get("symbol", ""))
        symbol = normalize_symbol(raw_symbol)

        # Acknowledge but ignore non-trades (prevents TV showing failures)
        if action not in ("BUY", "SELL"):
            app.logger.info("Non-trade payload ignored: %s", data)
            log_webhook_hit(request.path, hdrs, raw_data, data, 200)
            return jsonify({"ok": True, "note": "ignored (no BUY/SELL)"}), 200

        if symbol not in ALLOWED_SYMBOLS:
            app.logger.error("Reject: unsupported symbol raw='%s' -> '%s'", raw_symbol, symbol)
            log_webhook_hit(request.path, hdrs, raw_data, data, 400)
            return jsonify({"error": f"Unsupported symbol '{raw_symbol}' after normalization -> '{symbol}'"}), 400

        # ---- Quantity ----
        qty = data.get("qty", data.get("quantity"))
        if qty is None:
            qty = DEFAULT_QTY.get(symbol, 0.001)
        try:
            qty = float(qty)
            assert qty > 0
        except Exception:
            app.logger.error("Reject: invalid qty '%s'", data.get("qty"))
            log_webhook_hit(request.path, hdrs, raw_data, data, 400)
            return jsonify({"error": "Invalid qty"}), 400

        # ---- Optional passthroughs (for logging only) ----
        entry_price = float(data.get("entry_price", 0) or 0)
        sl_price    = float(data.get("sl_price", 0) or 0)
        tp_price    = float(data.get("tp_price", 0) or 0)

        # ---- Place order ----
        res = place_market_order_with_fallback(action, symbol, qty)

        # ---- Log to trades table ----
        log_trade({
            "action": action, "symbol": symbol, "qty": qty,
            "entry_price": entry_price, "sl_price": sl_price, "tp_price": tp_price,
            "status": "success" if res.get("ok") else "error",
            "error": "" if res.get("ok") else res.get("error", ""),
            "client_id": res.get("client_id", ""), "note": res.get("note", "")
        })

        # ---- Persist webhook hit and return ----
        if res.get("ok"):
            out = {"success": True, "client_id": res.get("client_id"), "order": res.get("order")}
            app.logger.info("Order OK: %s %s qty=%s client_id=%s", action, symbol, qty, res.get("client_id"))
            log_webhook_hit(request.path, hdrs, raw_data, data, 200)
            return jsonify(out), 200
        else:
            out = {"error": res.get("error", "Unknown error")}
            app.logger.error("Order FAIL: %s", res.get("error"))
            log_webhook_hit(request.path, hdrs, raw_data, data, 502)
            return jsonify(out), 502

    except Exception as e:
        app.logger.exception("Webhook fatal error")
        # log whatever we have
        try:
            hdrs = dict(request.headers)
        except Exception:
            hdrs = {}
        raw = ""
        try:
            raw = request.get_data(as_text=True)
        except Exception:
            pass
        log_webhook_hit(request.path if request else "unknown", hdrs, raw, {"error": str(e)}, 400)
        return jsonify({"error": str(e)}), 400

@app.route('/webhook', methods=['POST'])
def webhook():
    return _handle_tv()

@app.route('/tv', methods=['POST'])  # alias endpoint (handy if your TV alert uses /tv)
def tv():
    return _handle_tv()

@app.route("/env-debug")
def env_debug():
    keys = ["BINANCE_API_KEY", "BINANCE_API_SECRET", "PYTHONUNBUFFERED"]
    def mask(v): 
        return v and (v[:3] + "…" + v[-3:]) or "MISSING"
    seen = {k: mask(os.getenv(k)) for k in keys}
    return {"seen": seen, "all_keys_present": [k for k in os.environ.keys()]}

# =========================
# Entry point
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    # For production, prefer Gunicorn (see note below). This is fine for local/dev.
    app.run(host="0.0.0.0", port=port)
