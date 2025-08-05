from flask import Flask, request
import ccxt
import os

app = Flask(__name__)

API_KEY = os.environ.get("API_KEY", "your_api_key_here")
API_SECRET = os.environ.get("API_SECRET", "your_api_secret_here")

exchange = ccxt.binance({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
})

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json

    try:
        action = data.get("action")
        symbol = data.get("symbol").upper()
        qty = float(data.get("qty"))

        if action == "BUY":
            order = exchange.create_market_buy_order(symbol, qty)
            print(f"BUY order placed: {order}")
            return {"success": True, "order": order}

        elif action == "SELL":
            order = exchange.create_market_sell_order(symbol, qty)
            print(f"SELL order placed: {order}")
            return {"success": True, "order": order}

        else:
            return {"error": "Unknown action"}, 400

    except Exception as e:
        print("Webhook Error:", str(e))
        return {"error": str(e)}, 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)