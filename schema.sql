-- FitTrack — FIREFIT gym membership & class booking system

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS bookings;
DROP TABLE IF EXISTS classes;
DROP TABLE IF EXISTS members;
DROP TABLE IF EXISTS plans;
DROP TABLE IF EXISTS holiday_cache;

CREATE TABLE plans (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,
    price_eur         REAL NOT NULL CHECK (price_eur >= 0),
    duration_days     INTEGER NOT NULL CHECK (duration_days > 0),
    description       TEXT
);

CREATE TABLE members (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name   TEXT NOT NULL,
    last_name    TEXT NOT NULL,
    email        TEXT NOT NULL UNIQUE,
    phone        TEXT,
    plan_id      INTEGER NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
    start_date   TEXT NOT NULL,
    expiry_date  TEXT NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE classes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    instructor    TEXT NOT NULL,
    class_date    TEXT NOT NULL,              -- YYYY-MM-DD
    start_time    TEXT NOT NULL,              -- HH:MM
    end_time      TEXT NOT NULL,              -- HH:MM
    capacity      INTEGER NOT NULL CHECK (capacity > 0),
    room          TEXT,
    is_cancelled  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE bookings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    class_id  INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    booked_at TEXT DEFAULT CURRENT_TIMESTAMP,
    status    TEXT NOT NULL DEFAULT 'booked'
              CHECK (status IN ('booked','attended','no_show','cancelled')),
    UNIQUE (member_id, class_id)
);

CREATE TABLE holiday_cache (
    holiday_date TEXT PRIMARY KEY,
    name         TEXT,
    local_name   TEXT,
    types        TEXT,             -- CSV, e.g. "Public" or "Bank,School"
    is_closure   INTEGER NOT NULL DEFAULT 0,
    country_code TEXT,
    fetched_at   TEXT NOT NULL
);

CREATE INDEX idx_members_email   ON members(email);
CREATE INDEX idx_classes_date    ON classes(class_date);
CREATE INDEX idx_bookings_member ON bookings(member_id);
CREATE INDEX idx_bookings_class  ON bookings(class_id);
