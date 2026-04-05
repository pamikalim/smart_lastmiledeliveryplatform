# Smart Last Mile Delivery System

A microservices-based last mile delivery orchestration system built with Flask, RabbitMQ, MySQL, and OutSystems.

## Services

### Atomic Services
| Service | Port | Description |
|---------|------|-------------|
| Pricing Service | 5003 | Calculates delivery price via Google Maps |
| Payment Service | 5004 | Handles payment authorisation and refunds via Stripe |
| Rider Service | 5005 | Manages rider profiles and availability |

### Composite Services (Orchestrators)
| Service | Port | Description |
|---------|------|-------------|
| Accept Job Service | 5006 | Orchestrates rider job acceptance (Scenario 2) |
| Notification Service | 5007 | Consumes RabbitMQ events and sends SMS via Twilio |
| Place Order Service | 5008 | Orchestrates customer delivery request (Scenario 1) |
| Cancel Order Service | 5009 | Orchestrates order cancellation and refund (Scenario 3) |

### External Services (OutSystems)
| Service | Base URL |
|---------|----------|
| Customer Service | `https://personal-lqx7hrfq.outsystemscloud.com/Customer/rest/Customer` |
| Delivery Service | `https://personal-lqx7hrfq.outsystemscloud.com/Delivery/rest/Delivery` |

## Running the System

### Prerequisites
- Docker Desktop installed and running
- `.env` files configured for each service (see `.env.example` in each service folder)

### Start all services
```bash
docker-compose up --build
```

### Stop all services
```bash
docker-compose down
```

### Stop and wipe database volumes (fresh start)
```bash
docker-compose down -v
```

## RabbitMQ

- Management UI: http://localhost:15672 (login: guest / guest)
- Exchange: `delivery_topic` (topic exchange)

| Routing Key | Published by | Consumed by | Trigger |
|-------------|-------------|-------------|---------|
| `delivery.new` | Place Order | Notification | New order — SMS all available riders |
| `delivery.accepted` | Accept Job | Notification | Rider accepted — SMS rider + customer |
| `delivery.cancelled` | Cancel Order | Notification | Order cancelled — SMS rider + customer |

## User Scenarios

### Scenario 1 — Place Order
`POST http://localhost:5008/api/placeorder`
```json
{
  "customer_id": 7,
  "pickup_address": "1 Raffles Place, Singapore",
  "dropoff_address": "Orchard Road, Singapore",
  "pickup_lat": 1.2841,
  "pickup_lng": 103.8513,
  "dropoff_lat": 1.3048,
  "dropoff_lng": 103.8318,
  "package_details": "small"
}
```

### Scenario 2 — Accept Job
`POST http://localhost:5006/api/acceptbooking`
```json
{
  "rider_id": "r-001",
  "delivery_id": "123"
}
```

### Scenario 3 — Cancel Order
`PUT http://localhost:5009/api/cancelorder/{order_id}`
