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

        # Validate symbol format
        if not re.match(r"^[A-Z0-9]{1,20}$", symbol):
            return jsonify({"error": "Invalid symbol format"}), 400

        if action == "BUY":
            entry_price = float(data.get("entry_price"))
            tp_price = float(data.get("tp_price"))
            sl_price = float(data.get("sl_price"))

            print(f"Placing BUY order for {symbol}, qty: {qty}")
            buy_order = client.order_market_buy(symbol=symbol, quantity=qty)

            print("Buy order placed. Now placing OCO for SL/TP...")
            oco_order = client.create_oco_order(
                symbol=symbol,
                side=Client.SIDE_SELL,
                quantity=qty,
                price=round(tp_price, 2),
                stopPrice=round(sl_price * 1.01, 2),  # must be > stopLimitPrice
                stopLimitPrice=round(sl_price, 2),
                stopLimitTimeInForce=Client.TIME_IN_FORCE_GTC
            )

            return jsonify({
                "success": True,
                "buy_order": buy_order,
                "oco_order": oco_order
            })

        elif action == "SELL":
            print(f"Placing SELL order for {symbol}, qty: {qty}")
            sell_order = client.order_market_sell(symbol=symbol, quantity=qty)
            return jsonify({"success": True, "sell_order": sell_order})

        else:
            return jsonify({"error": "Invalid action"}), 400

    except Exception as e:
        print("Error:", str(e))
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
