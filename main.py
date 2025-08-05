print("API_KEY:", API_KEY)
print("API_SECRET:", API_SECRET)


from flask import Flask, request, jsonify
from binance.client import Client
import os

app = Flask(__name__)

# Get API keys from environment variables
API_KEY = os.environ.get("API_KEY")
API_SECRET = os.environ.get("API_SECRET")

# Force Binance.US endpoint
client = Client(API_KEY, API_SECRET, tld='us')

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    try:
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
        return jsonify({"error": str(e)}), 500

@app.route("/", methods=["GET"])
def home():
    return "Webhook bot is running!"
