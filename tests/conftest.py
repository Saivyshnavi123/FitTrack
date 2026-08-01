"""Shared fixtures and helpers. Each test gets its own SQLite file in
tmp_path, created fresh from schema.sql, so tests never share state and never
touch the real fittrack.db. Nothing in the suite touches the network.
"""
import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app                    # noqa: E402
from config import Config                     # noqa: E402
from database import connect, init_db         # noqa: E402
from seed import seed as seed_sample_data     # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)

@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path, Config.SCHEMA)
    return path

@pytest.fixture
def app(db_path):
    return create_app({"TESTING": True, "DATABASE": db_path})

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def db(db_path):
    conn = connect(db_path)
    yield conn
    conn.close()

@pytest.fixture
def seeded(tmp_path):
    path = str(tmp_path / "seeded.db")
    seed_sample_data(path, Config.SCHEMA)
    return path

@pytest.fixture
def seeded_client(seeded):
    return create_app({"TESTING": True, "DATABASE": seeded}).test_client()

@pytest.fixture
def no_holidays():
    """Every date is an ordinary trading day."""
    with patch("services.holiday_service._session.get") as mock_get:
        mock_get.return_value.json.return_value = []
        mock_get.return_value.raise_for_status.return_value = None
        yield mock_get

@pytest.fixture
def nager():
    """The real captured 2027 Irish holiday payload."""
    with patch("services.holiday_service._session.get") as mock_get:
        mock_get.return_value.json.return_value = load_fixture("nager_ie_2027.json")
        mock_get.return_value.raise_for_status.return_value = None
        yield mock_get

@pytest.fixture
def make_plan(client):
    def _make(**over):
        payload = {"name": "Test Plan", "price_eur": 50.0, "duration_days": 30,
                   "description": "A plan used by tests"}
        payload.update(over)
        resp = client.post("/api/plans", json=payload)
        assert resp.status_code == 201, resp.get_json()
        return resp.get_json()["data"]
    return _make

@pytest.fixture
def make_member(client, make_plan):
    def _make(**over):
        plan_id = over.pop("plan_id", None) or make_plan(
            name=f"Plan for {over.get('email', 'member')}")["id"]
        payload = {"first_name": "Aoife", "last_name": "Byrne",
                   "email": "aoife.byrne@example.com", "phone": "085 123 4401",
                   "plan_id": plan_id, "start_date": "2026-07-15"}
        payload.update(over)
        resp = client.post("/api/members", json=payload)
        assert resp.status_code == 201, resp.get_json()
        return resp.get_json()["data"]
    return _make

@pytest.fixture
def make_class(client, no_holidays):
    def _make(**over):
        payload = {"name": "Strength Circuit", "instructor": "Dave O'Brien",
                   "class_date": "2026-09-07", "start_time": "18:00",
                   "end_time": "19:00", "capacity": 10, "room": "Knocklyon"}
        payload.update(over)
        resp = client.post("/api/classes", json=payload)
        assert resp.status_code == 201, resp.get_json()
        return resp.get_json()["data"]
    return _make

# search and sort are not in the PoC, so seeded lookups filter here
def member_id(client, surname):
    found = [m for m in client.get("/api/members").get_json()["data"]
             if m["last_name"] == surname]
    assert len(found) == 1, f"expected one member named {surname}"
    return found[0]["id"]

def class_id(client, date, name=None):
    found = client.get(
        f"/api/classes?date={date}&include_cancelled=true").get_json()["data"]
    if name:
        found = [c for c in found if c["name"] == name]
    assert found, f"no class on {date} named {name}"
    return found[0]["id"]

def book(client, member, klass):
    return client.post("/api/bookings",
                       json={"member_id": member, "class_id": klass})

def refused(client, member, klass, error, status=409):
    """Book, expect the named rule to fire, return the response."""
    resp = book(client, member, klass)
    assert resp.status_code == status, resp.get_json()
    assert resp.get_json()["error"] == error
    return resp
