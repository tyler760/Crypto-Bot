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

# ---------- Defaults ----------
DEFAULT_QTY = {
    "BTCUSDT": 0.00025,  # tiny test sizes; adjust as you like
    "ETHUSDT": 0.005
}

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
ALLOWED_SYMBOLS = {"BTCUSDT", "ETHUSDT", "BTCUSD", "ETHUSD"}

def normalize_symbol(s: str) -> str:
    """
    Accepts TV formats like 'BINANCE:BTCUSDT' or 'BINANCEUS:ETHUSD' and returns
    a tradable symbol for Binance.US. Also maps USD->USDT if needed.
    """
    s = (s or "").strip().upper()
    if ":" in s:
        s = s.split(":")[-1].strip()
    if s not in ALLOWED_SYMBOLS:
        if s.endswith("USD"):
            maybe_usdt = s[:-3] + "USDT"
            if maybe_usdt in {"BTCUSDT", "ETHUSDT"}:
                s = maybe_usdt
    return s

def place_market_order_with_fallback(side: str, symbol: str, qty: float):
    """
    Place a MARKET order. If Binance.US returns a non-JSON body (HTML/404)
    even though the order fills, recover by fetching via our newClientOrderId.
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

        # Binance.US sometimes returns HTML '404 Not found' or 'Invalid JSON' though the order filled.
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

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # ---- Log raw + parsed for debugging ----
        raw_data = request.get_data(as_text=True)
        print("=== RAW WEBHOOK PAYLOAD ===")
        print(raw_data)
        print("===========================")

        data = request.get_json(force=True, silent=False)
        print("=== PARSED JSON DATA ===")
        print(data)
        print("========================")

        # ---- Basic validations ----
        action = str(data.get("action", "")).upper()
        raw_symbol = str(data.get("symbol", ""))
        symbol = normalize_symbol(raw_symbol)

        if action not in ("BUY", "SELL"):
            print(f"Rejecting: invalid action '{action}'")
            return jsonify({"error": "Invalid 'action' (use BUY or SELL)"}), 400

        if symbol not in ALLOWED_SYMBOLS:
            print(f"Rejecting: unsupported symbol raw='{raw_symbol}' -> normalized='{symbol}'")
            return jsonify({"error": f"Unsupported symbol '{raw_symbol}' after normalization -> '{symbol}'"}), 400

        # Accept qty if sent, else default by symbol
        qty = data.get("qty", data.get("quantity", None))
        if qty is None:
            qty = DEFAULT_QTY.get(symbol, 0.001)
        try:
            qty = float(qty)
            if qty <= 0:
                raise ValueError()
        except Exception:
            return jsonify({"error": "Invalid qty"}), 400

        # Optional passthroughs (for logging only)
        entry_price = float(data.get("entry_price", 0) or 0)
        sl_price    = float(data.get("sl_price", 0) or 0)
        tp_price    = float(data.get("tp_price", 0) or 0)

        # ---- Place order with resilient fallback ----
        res = place_market_order_with_fallback(action, symbol, qty)

        # ---- Log to DB ----
        log_trade({
            "action": action,
            "symbol": symbol,
            "qty": qty,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "status": "success" if res.get("ok") else "error",
            "error": "" if res.get("ok") else res.get("error", ""),
            "client_id": res.get("client_id", ""),
            "note": res.get("note", "")
        })

        if res.get("ok"):
            return jsonify({
                "success": True,
                "client_id": res.get("client_id"),
                "note": res.get("note", ""),
                "order": res.get("order")
            }), 200
        else:
            return jsonify({
                "error": res.get("error", "Unknown error"),
                "client_id": res.get("client_id", "")
            }), 502

    except Exception as e:
        print("Webhook Error:", e)
        return jsonify({"error": str(e)}), 400

# ---------- Entry point ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
