from flask import Flask, request, jsonify
from binance.client import Client
import os
import re

app = Flask(__name__)

# Get Binance API credentials from environment
API_KEY = os.environ.get("API_KEY")
API_SECRET = os.environ.get("API_SECRET")

# Show credentials in logs for debugging (optional)
print("API_KEY:", API_KEY)
print("API_SECRET:", "HIDDEN" if API_SECRET else None)

# Create Binance client for US accounts
client = Client(API_KEY, API_SECRET, tld='us')

@app.route("/", methods=["GET"])
def home():
    return "Webhook bot is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json
        print("Webhook data received:", data)

        action = data.get("action")
        symbol = data.get("symbol")
        qty = float(data.get("qty"))

        # ✅ Validate symbol format: only A-Z and 0–9, up to 20 characters
        if not re.match(r"^[A-Z0-9]{1,20}$", symbol):
            return jsonify({"error": "Invalid symbol format"}), 400

        if action == "BUY":
            print(f"Placing BUY order for {symbol}, qty: {qty}")
            order = client.order_market_buy(symbol=symbol, quantity=qty)
            return jsonify({"success": True, "order": order})

        elif action == "SELL":
            print(f"Placing SELL order for {symbol}, qty: {qty}")
            order = client.order_market_sell(symbol=symbol, quantity=qty)
            return jsonify({"success": True, "order": order})

        else:
            return jsonify({"error": "Invalid action"}), 400

    except Exception as e:
        print("Error:", str(e))
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
