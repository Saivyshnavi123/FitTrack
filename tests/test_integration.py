"""Full-stack integration tests.

Nager.Date is mocked with the REAL captured payload; everything else is real —
real Flask routing, real SQLite, real business logic. Assertions are made
against the database as well as through the API, because a 200 response proves
nothing on its own about what was actually stored. Response-body detail already
covered by test_external.py and test_classes.py is not repeated here.
"""
from unittest.mock import patch

import requests

from app import create_app
from conftest import load_fixture
from database import connect

def test_full_stack_lifecycle_asserted_against_sqlite(client, db_path, nager):
    plan = client.post("/api/plans", json={"name": "Annual Unlimited", "price_eur": 799.0,
                                           "duration_days": 365}).get_json()["data"]
    member = client.post("/api/members", json={
        "first_name": "Niamh", "last_name": "O'Connor", "email": "niamh.oconnor@example.com",
        "phone": "087 123 4403", "plan_id": plan["id"], "start_date": "2026-07-01"}).get_json()["data"]
    created = client.post("/api/classes", json={
        "name": "Reformer Flow", "instructor": "Laura Fitzgerald", "class_date": "2027-03-18",
        "start_time": "18:00", "end_time": "19:00", "capacity": 8, "room": "Churchtown"})
    assert created.status_code == 201
    class_id = created.get_json()["data"]["id"]
    assert nager.call_count == 1     # consulted server-side, not by the browser

    conn = connect(db_path)
    plan_row = conn.execute("SELECT * FROM plans WHERE id=?", (plan["id"],)).fetchone()
    assert (plan_row["name"], plan_row["duration_days"]) == ("Annual Unlimited", 365)
    member_row = conn.execute("SELECT * FROM members WHERE id=?", (member["id"],)).fetchone()
    # expiry was calculated from the plan duration and STORED, not derived
    assert (member_row["email"], member_row["expiry_date"], member_row["plan_id"]) \
        == ("niamh.oconnor@example.com", "2027-07-01", plan["id"])
    class_row = conn.execute("SELECT * FROM classes WHERE id=?", (class_id,)).fetchone()
    assert (class_row["name"], class_row["class_date"], class_row["capacity"],
            class_row["room"], class_row["is_cancelled"]) == ("Reformer Flow", "2027-03-18", 8, "Churchtown", 0)

    # the closure check ran and cached the whole year, is_closure computed on
    # insert so every later check is a local lookup
    assert len(conn.execute("SELECT * FROM holiday_cache WHERE substr(holiday_date,1,4)='2027'").fetchall()) == 11
    paddys = conn.execute("SELECT * FROM holiday_cache WHERE holiday_date='2027-03-17'").fetchone()
    good_friday = conn.execute("SELECT * FROM holiday_cache WHERE holiday_date='2027-03-26'").fetchone()
    assert (paddys["is_closure"], paddys["types"]) == (1, "Public")
    assert (good_friday["is_closure"], good_friday["types"]) == (0, "Bank,School")
    assert good_friday["local_name"] == "Aoine an Chéasta"       # UTF-8 intact
    conn.close()

    booking = client.post("/api/bookings", json={"member_id": member["id"], "class_id": class_id})
    assert booking.status_code == 201 and booking.get_json()["data"]["spaces_left"] == 7

    conn = connect(db_path)
    row = conn.execute("SELECT * FROM bookings WHERE id=?", (booking.get_json()["data"]["id"],)).fetchone()
    assert (row["member_id"], row["class_id"], row["status"]) == (member["id"], class_id, "booked")
    assert row["booked_at"] is not None
    assert conn.execute("SELECT COUNT(*) AS n FROM bookings WHERE class_id=?"
                        " AND status IN ('booked','attended')", (class_id,)).fetchone()["n"] == 1
    conn.close()

    # GET returns exactly what the frontend renders
    detail = client.get(f"/api/classes/{class_id}").get_json()["data"]
    assert (detail["booked_count"], detail["spaces_left"], detail["is_full"]) == (1, 7, False)
    assert detail["bookings"][0]["email"] == "niamh.oconnor@example.com"
    view = client.get(f"/api/members/{member['id']}").get_json()["data"]
    assert (view["status"], view["plan_name"]) == ("active", "Annual Unlimited")
    assert view["bookings"][0]["class_name"] == "Reformer Flow"

    # NFR1/NFR6: index.html is delivered as a static file, and every error path
    # answers with the JSON envelope rather than an HTML page
    assert b"<!doctype html" in client.get("/").data.lower()
    assert client.get("/api/nope").get_json()["success"] is False          # 404
    assert client.delete("/api/plans").get_json()["success"] is False      # 405
    broken = create_app({"DATABASE": "/nope/x.db", "PROPAGATE_EXCEPTIONS": False})
    broken.logger.disabled = True
    assert broken.test_client().get("/api/plans").get_json()["success"] is False   # 500

def test_full_stack_closure_paths_and_graceful_degradation(client, db_path):
    def schedule(day, instructor="Dave O'Brien", start="18:00", end="19:00"):
        return client.post("/api/classes", json={
            "name": "Strength Circuit", "instructor": instructor, "class_date": day,
            "start_time": start, "end_time": end, "capacity": 10, "room": "Knocklyon"})

    with patch("services.holiday_service._session.get") as up:
        up.return_value.json.return_value = load_fixture("nager_ie_2027.json")
        up.return_value.raise_for_status.return_value = None
        assert schedule("2027-03-17").status_code == 409           # Public: blocked
        allowed = schedule("2027-03-26", "Mark Ryan", "18:30", "19:30")
        assert allowed.status_code == 201                          # Good Friday: allowed
        assert "Good Friday" in allowed.get_json()["data"]["advisory"]

    # only the Good Friday class exists: the closure day wrote nothing
    conn = connect(db_path)
    assert [r["class_date"] for r in conn.execute("SELECT class_date FROM classes").fetchall()] == ["2027-03-26"]
    conn.close()

    # graceful degradation (NFR4): a Nager failure for an uncached year does not
    # block scheduling
    with patch("services.holiday_service._session.get", side_effect=requests.ConnectionError("down")):
        degraded = schedule("2029-03-17")
    assert degraded.status_code == 201
    assert degraded.get_json()["data"]["holiday_check"]["source"] == "unavailable"

    conn = connect(db_path)
    assert conn.execute("SELECT COUNT(*) AS n FROM classes").fetchone()["n"] == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM holiday_cache"
                        " WHERE substr(holiday_date,1,4)='2029'").fetchone()["n"] == 0
    conn.close()

def test_full_stack_scheduling_and_booking_rules_with_cancellation(client, db_path, nager):
    def schedule(instructor, start, end, room="Knocklyon"):
        return client.post("/api/classes", json={
            "name": "Strength Circuit", "instructor": instructor, "class_date": "2027-03-18",
            "start_time": start, "end_time": end, "capacity": 2, "room": room})

    class_id = schedule("Mark Ryan", "18:00", "19:00").get_json()["data"]["id"]
    assert schedule("Mark Ryan", "18:30", "19:30").status_code == 409      # clash
    assert schedule("Mark Ryan", "19:00", "20:00").status_code == 201      # back-to-back
    assert schedule("Laura Fitzgerald", "18:00", "19:00", "Churchtown").status_code == 201

    conn = connect(db_path)
    stored = [(r["instructor"], r["start_time"]) for r in
              conn.execute("SELECT instructor, start_time FROM classes ORDER BY id").fetchall()]
    conn.close()
    # three classes, not four: the clash was never written
    assert stored == [("Mark Ryan", "18:00"), ("Mark Ryan", "19:00"), ("Laura Fitzgerald", "18:00")]

    plan = client.post("/api/plans", json={"name": "Unlimited Monthly", "price_eur": 89.0,
                                           "duration_days": 400}).get_json()["data"]
    members = [client.post("/api/members", json={
        "first_name": f"M{i}", "last_name": "Test", "email": f"m{i}@example.com",
        "plan_id": plan["id"], "start_date": "2026-07-01"}).get_json()["data"] for i in range(3)]

    def place(index):
        return client.post("/api/bookings",
                           json={"member_id": members[index]["id"], "class_id": class_id})

    def resize(capacity):
        return client.put(f"/api/classes/{class_id}", json={
            "name": "Strength Circuit", "instructor": "Mark Ryan", "class_date": "2027-03-18",
            "start_time": "18:00", "end_time": "19:00", "capacity": capacity, "room": "Knocklyon"})

    booked = [place(0), place(1)]
    assert [b.status_code for b in booked] == [201, 201]
    assert booked[1].get_json()["data"]["spaces_left"] == 0
    assert place(2).get_json()["error"] == "Class is full"          # rule 4
    assert resize(1).status_code == 409          # capacity below places taken

    # cancel frees the place, and the freed place can be taken
    cancelled = client.delete(f"/api/bookings/{booked[0].get_json()['data']['id']}")
    assert cancelled.get_json()["data"]["spaces_left"] == 1
    assert place(2).status_code == 201

    # rule 5 is checked AFTER capacity, so the class needs room before it surfaces
    assert resize(4).status_code == 200
    assert place(1).get_json()["error"] == "Already booked"

    conn = connect(db_path)
    statuses = [r["status"] for r in conn.execute(
        "SELECT status FROM bookings WHERE class_id=? ORDER BY id", (class_id,)).fetchall()]
    held = conn.execute("SELECT COUNT(*) AS n FROM bookings WHERE class_id=?"
                        " AND status IN ('booked','attended')", (class_id,)).fetchone()["n"]
    conn.close()
    assert statuses == ["cancelled", "booked", "booked"]
    assert held == 2                              # never over capacity
