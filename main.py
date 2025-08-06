from flask import Flask, request, jsonify
from binance.client import Client
import os

app = Flask(__name__)

# Get API keys from environment
API_KEY = os.environ.get("API_KEY")
API_SECRET = os.environ.get("API_SECRET")


# Use Binance.US endpoint
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

        if action == "BUY":
            order = client.order_market_buy(symbol=symbol, quantity=qty)
            return jsonify({"success": True, "order": order})

        elif action == "SELL":
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

