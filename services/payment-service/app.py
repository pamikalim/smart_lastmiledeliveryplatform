from flask import Flask, request, jsonify 
import uuid # used to generate unique paymentID
import mysql.connector
from dotenv import load_dotenv
import os
import stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

load_dotenv()
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "payment_db"),
}

def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

app = Flask(__name__) #creates flask application

# just for testing
@app.route("/", methods=["GET"])
def home():
    return "Payment service is running"

# Authorise payment
@app.route("/api/payments/authorise", methods=["POST"])
def authorise_payment():
    data = request.get_json() or {}

    delivery_id = data.get("delivery_id")
    customer_id = data.get("customer_id")
    amount = data.get("amount")
    currency = data.get("currency", "SGD").upper()

    if not delivery_id or not customer_id or amount is None:
        return jsonify({
            "error": "delivery_id, customer_id and amount are required"
        }), 400

    try:
        amount_float = float(amount)
        if amount_float <= 0:
            return jsonify({
                "error": "amount must be greater than 0"
            }), 400
    except ValueError:
        return jsonify({
            "error": "amount must be numeric"
        }), 400

    payment_id = str(uuid.uuid4())

    try:
        amount_cents = int(round(amount_float * 100))

        payment_intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency=currency.lower(),
            payment_method="pm_card_visa",
            confirm=True,
            automatic_payment_methods={
                "enabled": True,
                "allow_redirects": "never"
            }
        )

        status = "PAID" if payment_intent.status == "succeeded" else payment_intent.status
        provider_ref = payment_intent.id

        conn = get_db_connection()
        cursor = conn.cursor()

        sql = """
        INSERT INTO payments
        (payment_id, delivery_id, customer_id, amount, currency, status, provider_ref)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """

        values = (
            payment_id,
            delivery_id,
            customer_id,
            amount_float,
            currency,
            status,
            provider_ref
        )

        cursor.execute(sql, values)
        conn.commit()

        cursor.close()
        conn.close()

        return jsonify({
            "payment_id": payment_id,
            "delivery_id": delivery_id,
            "customer_id": customer_id,
            "amount": amount_float,
            "currency": currency,
            "status": status,
            "provider_ref": provider_ref
        }), 200

    except stripe.StripeError as e:
        return jsonify({
            "error": "Stripe payment failed",
            "details": str(e)
        }), 400

    except mysql.connector.Error as e:
        return jsonify({
            "error": "Database error while authorising payment",
            "details": str(e)
        }), 500
    
    
# Refund payment
@app.route("/api/payments/refund", methods=["POST"])
def refund_payment():
    data = request.get_json() or {}

    payment_id = data.get("payment_id")
    reason = data.get("reason", "unspecified")

    if not payment_id:
        return jsonify({
            "error": "payment_id is required"
        }), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT * FROM payments WHERE payment_id = %s",
            (payment_id,)
        )
        payment = cursor.fetchone()

        if not payment:
            cursor.close()
            conn.close()
            return jsonify({
                "error": "payment not found"
            }), 404

        cursor.execute(
            "UPDATE payments SET status = %s WHERE payment_id = %s",
            ("REFUNDED", payment_id)
        )
        conn.commit()

        cursor.close()
        conn.close()

        return jsonify({
            "payment_id": payment_id,
            "status": "REFUNDED",
            "reason": reason
        }), 200

    except mysql.connector.Error as e:
        return jsonify({
            "error": "Database error while refunding payment",
            "details": str(e)
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5004, debug=True)

