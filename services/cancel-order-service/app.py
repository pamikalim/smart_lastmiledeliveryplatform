"""
Cancel Order Composite Service  (port 5009)
────────────────────────────────────────────────────────────────────
Scenario 3: Customer cancels an order before delivery

Flow orchestrated here:
  1. Customer UI  →  PUT /api/cancelorder/{order_id}
  2. GET   delivery status from Delivery Service  — must not be picked_up/completed
  3. GET   customer details from Customer Service (phone for notification)
  4. PUT   delivery status → 'cancelled'  (Delivery Service)
  5. GET   rider details from Rider Service
  6. PUT   rider availability → 'available'  (Rider Service)
  7. POST  refund to Payment Service
  8. Publish delivery.cancelled event → RabbitMQ  (SMS rider + customer)
  9. Return cancellation summary to Customer UI
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
PAYMENT_SERVICE_URL  = os.getenv("PAYMENT_SERVICE_URL",  "http://localhost:5004")
RIDER_SERVICE_URL    = os.getenv("RIDER_SERVICE_URL",    "http://localhost:5005")
DELIVERY_SERVICE_URL = os.getenv("DELIVERY_SERVICE_URL", "https://personal-lqx7hrfq.outsystemscloud.com/Delivery/rest/Delivery")
CUSTOMER_SERVICE_URL = os.getenv("CUSTOMER_SERVICE_URL", "https://personal-lqx7hrfq.outsystemscloud.com/Customer/rest/Customer")
RABBITMQ_HOST        = os.getenv("RABBITMQ_HOST",        "localhost")

# Orders cannot be cancelled once in these states
NON_CANCELLABLE_STATUSES = {"picked_up", "completed"}


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
    return jsonify({"message": "Cancel Order service is running"}), 200


# ────────────────────────────────────────────────────────────────────
# PUT /api/cancelorder/{order_id}
# ────────────────────────────────────────────────────────────────────
@app.route("/api/cancelorder/<int:order_id>", methods=["PUT"])
def cancel_order(order_id):

    # ── Step 1: Get delivery record and check status ──────────────────
    try:
        delivery_resp = http.get(
            f"{DELIVERY_SERVICE_URL}/delivery/{order_id}/",
            timeout=5
        )
    except Exception as e:
        return jsonify({"error": f"Delivery Service unreachable: {str(e)}"}), 503

    if delivery_resp.status_code == 404:
        return jsonify({"error": "Delivery not found"}), 404

    delivery_data = delivery_resp.json().get("data", {})
    current_status = delivery_data.get("status")
    rider_id       = delivery_data.get("rider_id")
    customer_id    = delivery_data.get("customer_id")
    price          = delivery_data.get("price", 0)

    if current_status in NON_CANCELLABLE_STATUSES:
        return jsonify({
            "error": f"Order cannot be cancelled — current status is '{current_status}'."
        }), 409

    if current_status == "cancelled":
        return jsonify({"error": "Order is already cancelled."}), 409

    # ── Step 2: Get customer details (phone for notification) ─────────
    customer_phone = ""
    try:
        customer_resp = http.get(
            f"{CUSTOMER_SERVICE_URL}/customer/{customer_id}/",
            timeout=5
        )
        if customer_resp.status_code == 200:
            customer_phone = customer_resp.json().get("data", {}).get("phone_number", "")
    except Exception as e:
        print(f"[WARN] Could not fetch customer details: {e}")

    # ── Step 3: Update delivery status → 'cancelled' ──────────────────
    try:
        cancel_resp = http.put(
            f"{DELIVERY_SERVICE_URL}/delivery/{order_id}/status",
            json={"status": "cancelled", "rider_id": rider_id},
            timeout=5
        )
        if cancel_resp.status_code != 200:
            return jsonify({"error": "Failed to cancel delivery record"}), 500
    except Exception as e:
        return jsonify({"error": f"Delivery Service unreachable: {str(e)}"}), 503

    # ── Step 4: Free up the rider (if one was assigned) ───────────────
    rider_phone = ""
    rider_name  = ""
    if rider_id:
        try:
            rider_resp = http.get(f"{RIDER_SERVICE_URL}/api/riders/{rider_id}", timeout=5)
            if rider_resp.status_code == 200:
                rider_data  = rider_resp.json()
                rider_phone = rider_data.get("phone_number", "")
                rider_name  = rider_data.get("name", "")

            http.put(
                f"{RIDER_SERVICE_URL}/api/riders/{rider_id}/availability",
                json={"availability": "available"},
                timeout=5
            )
        except Exception as e:
            print(f"[WARN] Could not update rider availability: {e}")

    # ── Step 5: Refund payment ────────────────────────────────────────
    refund_amount = 0
    try:
        refund_resp = http.post(
            f"{PAYMENT_SERVICE_URL}/api/payments/refund",
            json={
                "payment_id": str(order_id),
                "reason": "Customer cancelled order"
            },
            timeout=5
        )
        if refund_resp.status_code == 200:
            refund_amount = price
            print(f"[Payment] Refund processed for order {order_id}: SGD {refund_amount}")
        else:
            print(f"[WARN] Refund failed for order {order_id}: {refund_resp.text}")
    except Exception as e:
        print(f"[WARN] Payment Service unreachable during refund: {e}")

    # ── Step 6: Publish delivery.cancelled to RabbitMQ (async) ────────
    publish_event("delivery.cancelled", {
        "event": "delivery.cancelled",
        "delivery_id": order_id,
        "customer_id": customer_id,
        "customer_phone": customer_phone,
        "rider_id": rider_id,
        "rider_phone": rider_phone,
        "rider_name": rider_name,
        "refund_amount": refund_amount,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })

    # ── Step 7: Return cancellation summary ───────────────────────────
    return jsonify({
        "status": "cancelled",
        "order_id": order_id,
        "refund_amount": refund_amount,
        "message": "Order cancelled successfully. Refund will be processed shortly."
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5009, debug=True)
