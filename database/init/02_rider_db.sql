CREATE DATABASE IF NOT EXISTS rider_db;
USE rider_db;

CREATE TABLE IF NOT EXISTS riders (
    rider_id        VARCHAR(36)     PRIMARY KEY,
    name            VARCHAR(100)    NOT NULL,
    phone_number    VARCHAR(20)     NOT NULL,
    email           VARCHAR(100)    NOT NULL,
    availability    ENUM('available', 'busy') NOT NULL DEFAULT 'available',
    current_lat     DECIMAL(10, 7)  DEFAULT 1.3521,
    current_lng     DECIMAL(10, 7)  DEFAULT 103.8198,
    vehicle_type    VARCHAR(20)     NOT NULL DEFAULT 'motorcycle',
    rating          DECIMAL(3, 2)   DEFAULT 5.00,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP
);

INSERT IGNORE INTO riders (rider_id, name, phone_number, email, availability, current_lat, current_lng, vehicle_type, rating) VALUES
('r-001', 'Ahmad Bin Ali',     '+6591234567', 'ahmad@demo.com',   'available', 1.2966, 103.7764, 'motorcycle', 4.80),
('r-002', 'Ravi Krishnan',     '+6598765432', 'ravi@demo.com',    'available', 1.3048, 103.8318, 'bicycle',    4.60),
('r-003', 'Tan Wei Ming',      '+6581234567', 'tanwm@demo.com',   'available', 1.2840, 103.8518, 'motorcycle', 4.90),
('r-004', 'Siti Binte Rahmat', '+6591112222', 'siti@demo.com',    'busy',      1.3190, 103.8700, 'motorcycle', 4.70),
('r-005', 'James Lim',         '+6587654321', 'jameslim@demo.com','available', 1.3000, 103.8000, 'bicycle',    4.50);
