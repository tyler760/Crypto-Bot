import json
from flask import Flask, request
from binance.client import Client
from binance.enums import *

app = Flask(__name__)

API_KEY = 'zcn5fp50x521NLTtUERjJR6sx1BrZZP5trKh2wtPIAKwPkWjdB40OtcKpbTmg2gg'
API_SECRET = 'E3P0e3zl8KQPOHrxcdskWZQC6WlPUNijYAaVTaV4bVrhlbTcKtGeJ1TCShUOjobR'

client = Client(API_KEY, API_SECRET, tld='us')

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json

    try:
        action = data.get("action")
        symbol = data.get("symbol")
        qty = float(data.get("qty"))

        if action == "BUY":
            entry_price = float(data.get("entry_price"))
            sl_price = float(data.get("sl_price"))
            tp_price = float(data.get("tp_price"))
            print(f"Placing BUY order for {symbol}, qty: {qty}")
            order = client.order_market_buy(
                symbol=symbol,
                quantity=qty
            )

            print("Buy order placed. Now placing OCO for SL/TP...")
            oco_order = client.create_oco_order(
                symbol=symbol,
                side=SIDE_SELL,
                quantity=qty,
                price=round(tp_price, 2),
                stopPrice=round(sl_price * 1.01, 2),  # stopPrice must be > stopLimitPrice
                stopLimitPrice=round(sl_price, 2),
                stopLimitTimeInForce=TIME_IN_FORCE_GTC
            )
            return {"success": True, "buy_order": order, "oco_order": oco_order}

        elif action == "SELL":
            print(f"Placing SELL order for {symbol}, qty: {qty}")
            sell_order = client.order_market_sell(
                symbol=symbol,
                quantity=qty
            )
            return {"success": True, "sell_order": sell_order}

        else:
            return {"error": "Invalid action"}, 400

    except Exception as e:
        print("Error:", e)
        return {"error": str(e)}, 500

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)

