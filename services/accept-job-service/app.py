"""
Accept Job Composite Service  (port 5006)
────────────────────────────────────────────────────────────────────
Scenario 2: Rider accepts a delivery job

Flow orchestrated here:
  1. Rider UI  →  POST /api/acceptbooking  {rider_id, delivery_id}
  2. GET  delivery status from Delivery Service        → must be 'offered'
  3. PUT  delivery status → 'accepted'  (optimistic lock via version)
  4. PUT  rider availability → 'busy'   (Rider Service)
  5. GET  rider details (phone) for notification
  6. POST payment authorisation                        (Payment Service)
     └─ if payment fails: rollback steps 3 & 4
  7. Publish delivery.accepted event → RabbitMQ        (async)
  8. Return success to Rider UI

Also includes:
  • Cron job at midnight: batch-process rider payouts for
    deliveries completed that day (Scenario 2 BTL)
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
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

load_dotenv()

app = Flask(__name__)
CORS(app)

# ── Service URLs (override via .env or docker-compose) ──────────────
DELIVERY_SERVICE_URL  = os.getenv("DELIVERY_SERVICE_URL",  "https://personal-lqx7hrfq.outsystemscloud.com/Delivery/rest/Delivery")
RIDER_SERVICE_URL     = os.getenv("RIDER_SERVICE_URL",     "http://localhost:5005")
PAYMENT_SERVICE_URL   = os.getenv("PAYMENT_SERVICE_URL",   "http://localhost:5004")
CUSTOMER_SERVICE_URL  = os.getenv("CUSTOMER_SERVICE_URL",  "https://personal-lqx7hrfq.outsystemscloud.com/Customer/rest/Customer")
RABBITMQ_HOST         = os.getenv("RABBITMQ_HOST",         "localhost")

# ── RabbitMQ helper ─────────────────────────────────────────────────
def publish_event(routing_key: str, payload: dict):
    """Fire-and-forget publish to delivery_topic exchange."""
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
            properties=pika.BasicProperties(delivery_mode=2)   # persistent
        )
        connection.close()
        print(f"[RabbitMQ] Published {routing_key}: {payload}")
    except Exception as e:
        # Notification failure must NOT block job acceptance
        print(f"[RabbitMQ] Publish failed (non-fatal): {e}")


# ── Health check ────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Accept Job service is running"}), 200


# ────────────────────────────────────────────────────────────────────
# POST /api/acceptbooking
# Body: { "rider_id": "r-001", "delivery_id": "d-uuid-001" }
# ────────────────────────────────────────────────────────────────────
@app.route("/api/acceptbooking", methods=["POST"])
def accept_booking():
    data = request.get_json() or {}
    rider_id    = data.get("rider_id")
    delivery_id = data.get("delivery_id")

    if not rider_id or not delivery_id:
        return jsonify({"error": "rider_id and delivery_id are required"}), 400

    # ── Step 1: Check delivery status ───────────────────────────────
    try:
        delivery_resp = http.get(
            f"{DELIVERY_SERVICE_URL}/api/deliveries/{delivery_id}/status",
            timeout=5
        )
    except Exception as e:
        return jsonify({"error": f"Delivery Service unreachable: {str(e)}"}), 503

    if delivery_resp.status_code == 404:
        return jsonify({"error": "Delivery not found"}), 404

    delivery_data = delivery_resp.json()
    current_status = delivery_data.get("status")

    if current_status != "offered":
        # Already taken or cancelled — concurrency protection
        return jsonify({
            "error": "Delivery no longer available.",
            "code": 409,
            "current_status": current_status
        }), 409

    # ── Step 2: Mark delivery as 'accepted' (optimistic lock) ───────
    try:
        update_delivery_resp = http.put(
            f"{DELIVERY_SERVICE_URL}/api/deliveries/{delivery_id}/status",
            json={"status": "accepted", "rider_id": rider_id},
            timeout=5
        )
    except Exception as e:
        return jsonify({"error": f"Delivery Service unreachable: {str(e)}"}), 503

    if update_delivery_resp.status_code == 409:
        # Another rider accepted first — race condition handled
        return jsonify({"error": "Delivery was just accepted by another rider.", "code": 409}), 409

    if update_delivery_resp.status_code != 200:
        return jsonify({"error": "Failed to update delivery status"}), 500

    updated_delivery = update_delivery_resp.json()

    # ── Step 3: Mark rider as 'busy' ────────────────────────────────
    try:
        http.put(
            f"{RIDER_SERVICE_URL}/api/riders/{rider_id}/availability",
            json={"availability": "busy"},
            timeout=5
        )
    except Exception as e:
        # Non-fatal for now — delivery still accepted
        print(f"[WARN] Could not update rider availability: {e}")

    # ── Step 4: Get rider details for notification & payout ─────────
    rider_details = {}
    try:
        rider_resp = http.get(f"{RIDER_SERVICE_URL}/api/riders/{rider_id}", timeout=5)
        if rider_resp.status_code == 200:
            rider_details = rider_resp.json()
    except Exception as e:
        print(f"[WARN] Could not fetch rider details: {e}")

    # ── Step 4b: Get customer details for notification ───────────────
    price = updated_delivery.get("price", 0)
    customer_id = updated_delivery.get("customer_id", "")
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

    # ── Step 5: Authorise payment ────────────────────────────────────
    payment_ok = False
    payment_data = {}

    try:
        payment_resp = http.post(
            f"{PAYMENT_SERVICE_URL}/api/payments/authorise",
            json={
                "delivery_id": delivery_id,
                "customer_id": customer_id,
                "amount": price,
                "currency": "SGD"
            },
            timeout=5
        )
        if payment_resp.status_code == 200:
            payment_ok = True
            payment_data = payment_resp.json()
        else:
            print(f"[WARN] Payment authorisation failed: {payment_resp.text}")
    except Exception as e:
        print(f"[WARN] Payment Service unreachable: {e}")

    # ── Step 5a: Rollback if payment fails ───────────────────────────
    if not payment_ok:
        try:
            http.put(
                f"{DELIVERY_SERVICE_URL}/api/deliveries/{delivery_id}/status",
                json={"status": "offered", "rider_id": None},
                timeout=5
            )
            http.put(
                f"{RIDER_SERVICE_URL}/api/riders/{rider_id}/availability",
                json={"availability": "available"},
                timeout=5
            )
        except Exception as e:
            print(f"[ROLLBACK ERROR] {e}")

        return jsonify({"error": "Payment authorisation failed. Job acceptance rolled back."}), 402

    # ── Step 6: Publish delivery.accepted to RabbitMQ (async) ────────
    publish_event("delivery.accepted", {
        "event": "delivery.accepted",
        "delivery_id": delivery_id,
        "rider_id": rider_id,
        "rider_name": rider_details.get("name", ""),
        "rider_phone": rider_details.get("phone_number", ""),
        "customer_id": customer_id,
        "customer_phone": customer_phone,
        "pickup_address": updated_delivery.get("pickup_address", ""),
        "dropoff_address": updated_delivery.get("dropoff_address", ""),
        "price": price,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })

    # ── Step 7: Return success to Rider UI ───────────────────────────
    return jsonify({
        "status": "accepted",
        "delivery_id": delivery_id,
        "pickup_address": updated_delivery.get("pickup_address", ""),
        "dropoff_address": updated_delivery.get("dropoff_address", ""),
        "customer_name": updated_delivery.get("customer_name", ""),
        "price": price,
        "payout": round(price * 0.8, 2),   # rider earns 80% of delivery price
        "payment_id": payment_data.get("payment_id", "")
    }), 200


# ────────────────────────────────────────────────────────────────────
# CRON JOB — Rider Payout at Midnight  (BTL)
# Checks all deliveries completed today and processes rider payouts
# This runs automatically every day at 00:00
# ────────────────────────────────────────────────────────────────────
def process_rider_payouts():
    """
    Batch payout job: runs at midnight daily.
    Fetches all deliveries with status='completed' that have not yet
    been paid out, then triggers payment capture for each rider.
    """
    print(f"[CRON] Running rider payout job at {datetime.utcnow().isoformat()}")
    try:
        # Get completed deliveries from Delivery Service
        resp = http.get(
            f"{DELIVERY_SERVICE_URL}/api/deliveries?status=completed&payout_status=pending",
            timeout=10
        )
        if resp.status_code != 200:
            print(f"[CRON] Could not fetch completed deliveries: {resp.text}")
            return

        deliveries = resp.json()
        if isinstance(deliveries, dict):
            deliveries = deliveries.get("deliveries", [])

        print(f"[CRON] Found {len(deliveries)} deliveries to pay out")

        for delivery in deliveries:
            delivery_id = delivery.get("delivery_id") or delivery.get("order_id")
            rider_id    = delivery.get("rider_id")
            price       = delivery.get("price", 0)
            payout      = round(price * 0.8, 2)

            if not rider_id:
                continue

            # Trigger payment capture (mark as fully paid)
            try:
                pay_resp = http.post(
                    f"{PAYMENT_SERVICE_URL}/api/payments/capture",
                    json={
                        "delivery_id": delivery_id,
                        "rider_id": rider_id,
                        "payout_amount": payout,
                        "currency": "SGD"
                    },
                    timeout=5
                )
                if pay_resp.status_code == 200:
                    print(f"[CRON] Payout processed for rider {rider_id}, delivery {delivery_id}: SGD {payout}")
                else:
                    print(f"[CRON] Payout failed for delivery {delivery_id}: {pay_resp.text}")
            except Exception as e:
                print(f"[CRON] Payment Service error for delivery {delivery_id}: {e}")

    except Exception as e:
        print(f"[CRON] Payout job error: {e}")


# ── Start scheduler ─────────────────────────────────────────────────
scheduler = BackgroundScheduler()
scheduler.add_job(
    func=process_rider_payouts,
    trigger="cron",
    hour=0,
    minute=0,
    id="midnight_payout"
)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5006, debug=True, use_reloader=False)
    # use_reloader=False is required when using APScheduler — 
    # Flask's reloader would start the scheduler twice
