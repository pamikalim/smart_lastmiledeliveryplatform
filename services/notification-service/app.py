"""
Notification Service  (port 5007)
────────────────────────────────────────────────────────────────────
Consumes events from RabbitMQ delivery_topic exchange and sends
SMS notifications via Twilio.

Events handled:
  • delivery.accepted   → SMS to rider + customer
  • delivery.new        → SMS to all available riders about new job
  • delivery.cancelled  → SMS to rider + customer about cancellation,
                          then SMS to customer about refund
────────────────────────────────────────────────────────────────────
"""

from flask import Flask, jsonify
from twilio.rest import Client
import pika
import json
import os
import threading
import time
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ── Twilio config ────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID  = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN   = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

# ── RabbitMQ config ──────────────────────────────────────────────────
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
EXCHANGE_NAME = "delivery_topic"
QUEUE_NAME    = "notification_queue"


# ── SMS helper ───────────────────────────────────────────────────────
def send_sms(to: str, body: str):
    """Send an SMS via Twilio. Logs and swallows errors so one bad
    number never kills the consumer loop."""
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=body,
            from_=TWILIO_PHONE_NUMBER,
            to=to
        )
        print(f"[Twilio] SMS sent to {to} — SID: {message.sid}")
    except Exception as e:
        print(f"[Twilio] Failed to send SMS to {to}: {e}")


# ── Event handlers ───────────────────────────────────────────────────
def handle_delivery_accepted(payload: dict):
    rider_name      = payload.get("rider_name", "Your rider")
    rider_phone     = payload.get("rider_phone")
    customer_phone  = payload.get("customer_phone")
    delivery_id     = payload.get("delivery_id")
    pickup_address  = payload.get("pickup_address")
    dropoff_address = payload.get("dropoff_address")
    price           = payload.get("price", 0)
    payout          = round(price * 0.8, 2)

    if rider_phone:
        send_sms(rider_phone, (
            f"Hi {rider_name}, you have accepted a delivery job!\n"
            f"Delivery ID: {delivery_id}\n"
            f"Pick up: {pickup_address}\n"
            f"Drop off: {dropoff_address}\n"
            f"Your payout: SGD {payout:.2f}"
        ))

    if customer_phone:
        send_sms(customer_phone, (
            f"Good news! Your delivery ({delivery_id}) has been accepted "
            f"by {rider_name}.\n"
            f"Pick up: {pickup_address}\n"
            f"Drop off: {dropoff_address}"
        ))


def handle_delivery_new(payload: dict):
    """Scenario 1 — notify all available riders of a new delivery job."""
    rider_phones    = payload.get("rider_phones", [])   # list of phone numbers
    delivery_id     = payload.get("delivery_id")
    pickup_address  = payload.get("pickup_address")
    dropoff_address = payload.get("dropoff_address")
    price           = payload.get("price", 0)
    payout          = round(price * 0.8, 2)

    for phone in rider_phones:
        send_sms(phone, (
            f"New delivery job available!\n"
            f"Delivery ID: {delivery_id}\n"
            f"Pick up: {pickup_address}\n"
            f"Drop off: {dropoff_address}\n"
            f"Payout: SGD {payout:.2f}\n"
            f"Open the app to accept."
        ))


def handle_delivery_cancelled(payload: dict):
    """Scenario 3 — notify rider + customer of cancellation and refund."""
    rider_phone    = payload.get("rider_phone")
    customer_phone = payload.get("customer_phone")
    delivery_id    = payload.get("delivery_id")
    refund_amount  = payload.get("refund_amount", 0)

    if rider_phone:
        send_sms(rider_phone, (
            f"Delivery {delivery_id} has been cancelled by the customer.\n"
            f"You are now available for new jobs."
        ))

    if customer_phone:
        send_sms(customer_phone, (
            f"Your delivery ({delivery_id}) has been cancelled.\n"
            f"A refund of SGD {refund_amount:.2f} will be returned to you shortly."
        ))


# ── RabbitMQ consumer ────────────────────────────────────────────────
def on_message(channel, method, properties, body):
    routing_key = method.routing_key
    try:
        payload = json.loads(body)
        print(f"[RabbitMQ] Received {routing_key}: {payload}")

        if routing_key == "delivery.accepted":
            handle_delivery_accepted(payload)
        elif routing_key == "delivery.new":
            handle_delivery_new(payload)
        elif routing_key == "delivery.cancelled":
            handle_delivery_cancelled(payload)
        else:
            print(f"[RabbitMQ] No handler for routing key: {routing_key}")

        channel.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as e:
        print(f"[RabbitMQ] Error processing message: {e}")
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


def start_consumer():
    """Connect to RabbitMQ and start consuming. Retries on failure."""
    while True:
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST)
            )
            channel = connection.channel()

            channel.exchange_declare(
                exchange=EXCHANGE_NAME,
                exchange_type="topic",
                durable=True
            )
            channel.queue_declare(queue=QUEUE_NAME, durable=True)

            # Bind all routing keys this service handles
            for routing_key in ["delivery.accepted", "delivery.new", "delivery.cancelled"]:
                channel.queue_bind(
                    queue=QUEUE_NAME,
                    exchange=EXCHANGE_NAME,
                    routing_key=routing_key
                )

            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=QUEUE_NAME, on_message_callback=on_message)

            print(f"[RabbitMQ] Listening on '{QUEUE_NAME}' (exchange: {EXCHANGE_NAME})")
            channel.start_consuming()

        except Exception as e:
            print(f"[RabbitMQ] Connection failed, retrying in 5s: {e}")
            time.sleep(5)


# ── Health check ─────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Notification service is running"}), 200


# ── Start consumer in background thread ──────────────────────────────
consumer_thread = threading.Thread(target=start_consumer, daemon=True)
consumer_thread.start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5007, debug=True, use_reloader=False)
