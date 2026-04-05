import os
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")


@app.route("/", methods=["GET"])
def home():
    return "Pricing service is running"


@app.route("/api/pricing/estimate", methods=["POST"])
def estimate_price():
    data = request.get_json() or {}

    pickup = data.get("pickup_location")
    dropoff = data.get("dropoff_location")
    package_type = data.get("package_type", "small")

    if not pickup or not dropoff:
        return jsonify({
            "error": "pickup_location and dropoff_location are required"
        }), 400

    if not GOOGLE_MAPS_API_KEY:
        return jsonify({
            "error": "Missing GOOGLE_MAPS_API_KEY in .env"
        }), 500

    maps_url = "https://maps.googleapis.com/maps/api/distancematrix/json"
    params = {
        "origins": pickup,
        "destinations": dropoff,
        "key": GOOGLE_MAPS_API_KEY
    }

    try:
        response = requests.get(maps_url, params=params, timeout=10)
        maps_data = response.json()

        if maps_data.get("status") != "OK":
            return jsonify({
                "error": "Google Maps request failed",
                "maps_status": maps_data.get("status"),
                "details": maps_data
            }), 400

        row = maps_data["rows"][0]["elements"][0]

        if row.get("status") != "OK":
            return jsonify({
                "error": "No valid route found",
                "route_status": row.get("status")
            }), 400

        distance_meters = row["distance"]["value"]
        duration_seconds = row["duration"]["value"]

        distance_km = round(distance_meters / 1000, 2)
        duration_min = round(duration_seconds / 60)

        base_fee = 3.0
        rate_per_km = 1.5
        surcharge_map = {
            "small": 1.0,
            "medium": 2.0,
            "large": 3.0
        }
        package_surcharge = surcharge_map.get(package_type.lower(), 1.0)

        price = round(base_fee + (distance_km * rate_per_km) + package_surcharge, 2)

        return jsonify({
            "pickup_location": pickup,
            "dropoff_location": dropoff,
            "package_type": package_type,
            "distance_km": distance_km,
            "duration_min": duration_min,
            "price": price,
            "currency": "SGD"
        }), 200

    except Exception as e:
        return jsonify({
            "error": "Pricing service failed",
            "details": str(e)
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5003, debug=True)