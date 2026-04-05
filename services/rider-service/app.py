from flask import Flask, request, jsonify
from flask_cors import CORS
import mysql.connector
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

def get_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "rider_db")
    )

# ─────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────
@app.route("/", methods=["GET"])
def home():
    return jsonify({"message": "Rider service is running"}), 200


# ─────────────────────────────────────────────
# GET /api/riders
# Returns all riders (admin / debug use)
# ─────────────────────────────────────────────
@app.route("/api/riders", methods=["GET"])
def get_all_riders():
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT rider_id, name, phone_number, email, availability, current_lat, current_lng, vehicle_type, rating, created_at FROM riders")
        riders = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify(riders), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# GET /api/riders/available
# Returns all riders with availability = 'available'
# Used by Place an Order (Scenario 1) and Accept Job (Scenario 2)
# ─────────────────────────────────────────────
@app.route("/api/riders/available", methods=["GET"])
def get_available_riders():
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT rider_id, name, phone_number, email, availability, current_lat, current_lng, vehicle_type, rating FROM riders WHERE availability = 'available'"
        )
        riders = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify(riders), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# GET /api/riders/<rider_id>
# Returns a single rider's details
# Used by Accept Job composite (to get rider phone for notification)
# ─────────────────────────────────────────────
@app.route("/api/riders/<rider_id>", methods=["GET"])
def get_rider(rider_id):
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT rider_id, name, phone_number, email, availability, current_lat, current_lng, vehicle_type, rating, created_at FROM riders WHERE rider_id = %s",
            (rider_id,)
        )
        rider = cursor.fetchone()
        cursor.close()
        conn.close()
        if not rider:
            return jsonify({"error": "Rider not found"}), 404
        return jsonify(rider), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# PUT /api/riders/<rider_id>/availability
# Updates rider availability: 'available' or 'busy'
# Called by Accept Job composite after rider accepts
# Called by Cancel Order composite to free up rider
# ─────────────────────────────────────────────
@app.route("/api/riders/<rider_id>/availability", methods=["PUT"])
def update_rider_availability(rider_id):
    data = request.get_json() or {}
    availability = data.get("availability")

    if availability not in ("available", "busy"):
        return jsonify({"error": "availability must be 'available' or 'busy'"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "UPDATE riders SET availability = %s WHERE rider_id = %s",
            (availability, rider_id)
        )
        conn.commit()
        if cursor.rowcount == 0:
            cursor.close()
            conn.close()
            return jsonify({"error": "Rider not found"}), 404

        cursor.execute(
            "SELECT rider_id, name, availability FROM riders WHERE rider_id = %s",
            (rider_id,)
        )
        updated = cursor.fetchone()
        cursor.close()
        conn.close()
        return jsonify(updated), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# GET /api/riders/<rider_id>/location
# Returns current lat/lng of a rider
# Used by Cancel Order composite (Scenario 3)
# ─────────────────────────────────────────────
@app.route("/api/riders/<rider_id>/location", methods=["GET"])
def get_rider_location(rider_id):
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT rider_id, current_lat, current_lng FROM riders WHERE rider_id = %s",
            (rider_id,)
        )
        rider = cursor.fetchone()
        cursor.close()
        conn.close()
        if not rider:
            return jsonify({"error": "Rider not found"}), 404
        return jsonify(rider), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# PUT /api/riders/<rider_id>/location
# Updates rider's current GPS coordinates
# (future use: live location tracking)
# ─────────────────────────────────────────────
@app.route("/api/riders/<rider_id>/location", methods=["PUT"])
def update_rider_location(rider_id):
    data = request.get_json() or {}
    lat = data.get("current_lat")
    lng = data.get("current_lng")

    if lat is None or lng is None:
        return jsonify({"error": "current_lat and current_lng are required"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE riders SET current_lat = %s, current_lng = %s WHERE rider_id = %s",
            (lat, lng, rider_id)
        )
        conn.commit()
        if cursor.rowcount == 0:
            cursor.close()
            conn.close()
            return jsonify({"error": "Rider not found"}), 404
        cursor.close()
        conn.close()
        return jsonify({"rider_id": rider_id, "current_lat": lat, "current_lng": lng}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# POST /api/riders
# Register a new rider
# ─────────────────────────────────────────────
@app.route("/api/riders", methods=["POST"])
def create_rider():
    data = request.get_json() or {}
    required = ["rider_id", "name", "phone_number", "email", "vehicle_type"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"{field} is required"}), 400

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO riders (rider_id, name, phone_number, email, availability, current_lat, current_lng, vehicle_type, rating)
               VALUES (%s, %s, %s, %s, 'available', %s, %s, %s, 5.0)""",
            (
                data["rider_id"],
                data["name"],
                data["phone_number"],
                data["email"],
                data.get("current_lat", 1.3521),
                data.get("current_lng", 103.8198),
                data["vehicle_type"],
            )
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"rider_id": data["rider_id"], "message": "Rider created successfully"}), 201
    except mysql.connector.IntegrityError:
        return jsonify({"error": "Rider with this ID already exists"}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5005, debug=True)
