"""
Place Order Composite Service  (port 5008)
────────────────────────────────────────────────────────────────────
Scenario 1: Customer submits a delivery request

Flow orchestrated here:
  1. Customer UI  →  POST /api/placeorder  {customer_id, pickup, dropoff, package_details}
  2. POST  price estimate from Pricing Service
  3. GET   customer details from Customer Service
  4. POST  create delivery record in Delivery Service  (status: 'offered')
  5. POST  authorise payment from Payment Service
     └─ if payment fails: DELETE delivery record, return error
  6. GET   available riders from Rider Service
  7. Publish delivery.new event → RabbitMQ  (SMS all available riders)
  8. Return order summary to Customer UI
────────────────────────────────────────────────────────────────────
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests as http
import os
import pika
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# ── Service URLs ─────────────────────────────────────────────────────
PRICING_SERVICE_URL  = os.getenv("PRICING_SERVICE_URL",  "http://localhost:5003")
PAYMENT_SERVICE_URL  = os.getenv("PAYMENT_SERVICE_URL",  "http://localhost:5004")
RIDER_SERVICE_URL    = os.getenv("RIDER_SERVICE_URL",    "http://localhost:5005")
DELIVERY_SERVICE_URL = os.getenv("DELIVERY_SERVICE_URL", "https://personal-lqx7hrfq.outsystemscloud.com/Delivery/rest/Delivery")
CUSTOMER_SERVICE_URL = os.getenv("CUSTOMER_SERVICE_URL", "https://personal-lqx7hrfq.outsystemscloud.com/Customer/rest/Customer")
RABBITMQ_HOST        = os.getenv("RABBITMQ_HOST",        "localhost")


# ── RabbitMQ helper ──────────────────────────────────────────────────
def publish_event(routing_key: str, payload: dict):
    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(host=RABBITMQ_HOST)
        )
        channel = connection.channel()
        channel.exchange_declare(exchange="delivery_topic", exchange_type="topic", durable=True)
        channel.basic_publish(
            exchange="delivery_topic",
            routing_key=routing_key,
            body=json.dumps(payload),
            properties=pika.BasicProperties(delivery_mode=2)
        )
        connection.close()
        print(f"[RabbitMQ] Published {routing_key}: {payload}")
    except Exception as e:
        print(f"[RabbitMQ] Publish failed (non-fatal): {e}")


# ── Health check ─────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Place Order service is running"}), 200


# ────────────────────────────────────────────────────────────────────
# POST /api/placeorder
# Body: {
#   "customer_id": 123,
#   "pickup_address": "...",
#   "dropoff_address": "...",
#   "pickup_lat": 1.3,
#   "pickup_lng": 103.8,
#   "dropoff_lat": 1.3,
#   "dropoff_lng": 103.8,
#   "package_details": "..."
# }
# ────────────────────────────────────────────────────────────────────
@app.route("/api/placeorder", methods=["POST"])
def place_order():
    data = request.get_json() or {}
    customer_id     = data.get("customer_id")
    pickup_address  = data.get("pickup_address")
    dropoff_address = data.get("dropoff_address")
    pickup_lat      = data.get("pickup_lat")
    pickup_lng      = data.get("pickup_lng")
    dropoff_lat     = data.get("dropoff_lat")
    dropoff_lng     = data.get("dropoff_lng")
    package_details = data.get("package_details", "")

    if not all([customer_id, pickup_address, dropoff_address]):
        return jsonify({"error": "customer_id, pickup_address and dropoff_address are required"}), 400

    # ── Step 1: Get price estimate ───────────────────────────────────
    try:
        pricing_resp = http.post(
            f"{PRICING_SERVICE_URL}/api/pricing/estimate",
            json={
                "pickup_location": pickup_address,
                "dropoff_location": dropoff_address,
                "package_type": package_details
            },
            timeout=5
        )
        if pricing_resp.status_code != 200:
            return jsonify({"error": "Failed to get price estimate"}), 502
        price = pricing_resp.json().get("price", 0)
    except Exception as e:
        return jsonify({"error": f"Pricing Service unreachable: {str(e)}"}), 503

    # ── Step 2: Get customer details ─────────────────────────────────
    try:
        customer_resp = http.get(
            f"{CUSTOMER_SERVICE_URL}/customer/{customer_id}/",
            timeout=5
        )
        if customer_resp.status_code != 200:
            return jsonify({"error": "Customer not found"}), 404
        customer_data = customer_resp.json().get("data", {})
        customer_phone = customer_data.get("phone_number", "")
        customer_name  = customer_data.get("name", "")
    except Exception as e:
        return jsonify({"error": f"Customer Service unreachable: {str(e)}"}), 503

    # ── Step 3: Create delivery record (status: 'offered') ───────────
    try:
        delivery_resp = http.post(
            f"{DELIVERY_SERVICE_URL}/delivery/",
            json={
                "customer_id": str(customer_id),
                "rider_id": "",
                "pickup_address": pickup_address,
                "dropoff_address": dropoff_address,
                "pickup_lat": pickup_lat,
                "pickup_lng": pickup_lng,
                "dropoff_lat": dropoff_lat,
                "dropoff_lng": dropoff_lng,
                "package_details": package_details,
                "price": price
            },
            timeout=5
        )
        if delivery_resp.status_code != 200:
            return jsonify({"error": "Failed to create delivery record"}), 502
        delivery_data = delivery_resp.json().get("data", {})
        order_id = delivery_data.get("order_id")
    except Exception as e:
        return jsonify({"error": f"Delivery Service unreachable: {str(e)}"}), 503

    # ── Step 4: Authorise payment ────────────────────────────────────
    try:
        payment_resp = http.post(
            f"{PAYMENT_SERVICE_URL}/api/payments/authorise",
            json={
                "delivery_id": str(order_id),
                "customer_id": str(customer_id),
                "amount": price,
                "currency": "SGD"
            },
            timeout=5
        )
        if payment_resp.status_code != 200:
            # Rollback: delete delivery record
            try:
                http.delete(f"{DELIVERY_SERVICE_URL}/delivery/{order_id}/", timeout=5)
            except Exception:
                pass
            return jsonify({"error": "Payment authorisation failed. Order not placed."}), 402
        payment_data = payment_resp.json()
    except Exception as e:
        return jsonify({"error": f"Payment Service unreachable: {str(e)}"}), 503

    # ── Step 5: Get available riders ─────────────────────────────────
    rider_phones = []
    try:
        riders_resp = http.get(f"{RIDER_SERVICE_URL}/api/riders/available", timeout=5)
        if riders_resp.status_code == 200:
            riders = riders_resp.json()
            rider_phones = [r.get("phone_number") for r in riders if r.get("phone_number")]
    except Exception as e:
        print(f"[WARN] Could not fetch available riders: {e}")

    # ── Step 6: Publish delivery.new to RabbitMQ (async) ─────────────
    publish_event("delivery.new", {
        "event": "delivery.new",
        "delivery_id": order_id,
        "customer_id": customer_id,
        "pickup_address": pickup_address,
        "dropoff_address": dropoff_address,
        "price": price,
        "rider_phones": rider_phones,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })

    # ── Step 7: Return order summary ─────────────────────────────────
    return jsonify({
        "status": "order_placed",
        "order_id": order_id,
        "customer_name": customer_name,
        "pickup_address": pickup_address,
        "dropoff_address": dropoff_address,
        "price": price,
        "payment_id": payment_data.get("payment_id", ""),
        "message": "Order placed successfully. Riders are being notified."
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5008, debug=True)
