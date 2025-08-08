import os
import re
import time
import uuid
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from binance.spot import Spot  # binance-connector (official), works with Binance.US

app = Flask(__name__)

# ---------- Binance.US client ----------
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
client = Spot(api_key=API_KEY, api_secret=API_SECRET, base_url="https://api.binance.us")

# ---------- SQLite setup ----------
DB_FILE = "trades.db"

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
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
init_db()

def log_trade(row):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "INSERT INTO trades (action, symbol, qty, entry_price, sl_price, tp_price, timestamp, status, error, client_id, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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

# ---------- Helpers ----------
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,20}$")

def place_market_order_with_fallback(side: str, symbol: str, qty: float):
    """
    Places a MARKET order. If Binance.US returns a non-JSON body (HTML/404)
    even though the order fills, recover by fetching the order via our own
    newClientOrderId.
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
        print("Primary order error:", msg)

        # Known Binance.US quirk: sometimes returns HTML '404 Not found' or 'Invalid JSON' though order filled.
        if "Invalid JSON error message" in msg or "404 Not found" in msg or "code=0" in msg:
            try:
                status = client.get_order(symbol=symbol, origClientOrderId=client_id)
                return {"ok": True, "order": status, "client_id": client_id, "note": "Recovered after parse error"}
            except Exception as e2:
                return {"ok": False, "error": f"Fallback lookup failed: {e2}", "client_id": client_id}
        else:
            return {"ok": False, "error": msg, "client_id": client_id}

# ---------- Routes ----------
@app.route("/", methods=["GET"])
def home():
    return "Bot is running (Binance.US)"

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

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    print("Webhook payload:", data)

    action = str(data.get("action", "")).upper()
    symbol = str(data.get("symbol", "")).upper()
    # Accept either 'qty' or 'quantity'
    qty_val = data.get("qty", data.get("quantity", None))
    try:
        qty = float(qty_val) if qty_val is not None else None
    except Exception:
        qty = None

    entry_price = float(data.get("entry_price", 0) or 0)
    sl_price = float(data.get("sl_price", 0) or 0)
    tp_price = float(data.get("tp_price", 0) or 0)

    # Basic validations
    if action not in ("BUY", "SELL"):
        return jsonify({"error": "Invalid action. Use BUY or SELL."}), 400
    if not SYMBOL_RE.match(symbol):
        return jsonify({"error": "Invalid symbol format"}), 400
    if qty is None or qty <= 0:
        return jsonify({"error": "Invalid or missing qty"}), 400

    # Place order with resilient fallback
    result = place_market_order_with_fallback(action, symbol, qty)

    # Build a log row
    log_row = {
        "action": action,
        "symbol": symbol,
        "qty": qty,
        "entry_price": entry_price,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "status": "success" if result.get("ok") else "error",
        "error": "" if result.get("ok") else result.get("error", ""),
        "client_id": result.get("client_id", ""),
        "note": result.get("note", "")
    }
    log_trade(log_row)

    if result.get("ok"):
        return jsonify({
            "success": True,
            "client_id": result["client_id"],
            "note": result.get("note", ""),
            "order": result.get("order")
        })
    else:
        return jsonify({
            "error": result.get("error", "Unknown error"),
            "client_id": result.get("client_id", "")
        }), 502

# ---------- Entry point ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
