"""Bookings: lifecycle, failure paths, and the six rules grouped by theme.
Rules are checked IN ORDER, first failure wins.
"""
import sqlite3
import threading

import pytest

from conftest import book, class_id, member_id, refused
from database import connect
from services.booking_rules import evaluate_booking

MEMBER = {"id": 1, "is_active": 1, "expiry_date": "2099-01-01"}
KLASS = {"id": 2, "is_cancelled": 0, "class_date": "2099-01-01",
         "start_time": "18:00", "end_time": "19:00", "capacity": 10}

def test_booking_lifecycle(seeded, seeded_client):
    killian, orla = member_id(seeded_client, "Brady"), member_id(seeded_client, "Sheridan")
    target = class_id(seeded_client, "2026-08-05", "Strength Circuit")

    # create: capacity 10 with Oisin already seeded on it, so 8 free after
    created = book(seeded_client, killian, target)
    assert created.status_code == 201
    booking = created.get_json()["data"]
    assert (booking["status"], booking["reactivated"], booking["spaces_left"]) == ("booked", False, 8)

    # read back, joined to its member and class
    fetched = seeded_client.get(f"/api/bookings/{booking['id']}").get_json()["data"]
    assert fetched["class_name"] == "Strength Circuit" and fetched["email"]

    # list, and filter by member and by class
    assert len(seeded_client.get("/api/bookings").get_json()["data"]) == 24
    assert all(b["member_id"] == killian for b in
               seeded_client.get(f"/api/bookings?member_id={killian}").get_json()["data"])
    assert len(seeded_client.get(f"/api/bookings?class_id={target}").get_json()["data"]) == 2

    # update the status, and read it back
    assert seeded_client.patch(f"/api/bookings/{booking['id']}/status",
                               json={"status": "attended"}).status_code == 200
    seeded_client.patch(f"/api/bookings/{booking['id']}/status", json={"status": "no_show"})
    assert seeded_client.get(f"/api/bookings/{booking['id']}").get_json()["data"]["status"] == "no_show"

    # Cancel sets the status rather than deleting the row, and the place is freed
    # at once because every count filters on booked/attended.
    full = class_id(seeded_client, "2026-08-11", "Reformer Flow")   # seeded 7 of 8
    filled_id = book(seeded_client, killian, full).get_json()["data"]["id"]
    cancelled = seeded_client.delete(f"/api/bookings/{filled_id}")
    assert (cancelled.get_json()["data"]["status"], cancelled.get_json()["data"]["spaces_left"]) == ("cancelled", 1)
    assert book(seeded_client, orla, full).status_code == 201

    # book -> cancel -> REBOOK reactivates the existing row. UNIQUE(member_id,
    # class_id) makes a second row impossible, so one auditable row per pair.
    freed = [b for b in seeded_client.get(f"/api/bookings?class_id={full}").get_json()["data"]
             if b["status"] == "booked"][0]
    seeded_client.delete(f"/api/bookings/{freed['id']}")
    revived = book(seeded_client, killian, full)
    assert revived.get_json()["data"]["reactivated"] is True
    assert revived.get_json()["data"]["id"] == filled_id      # the SAME row
    conn = connect(seeded)
    rows = conn.execute("SELECT * FROM bookings WHERE member_id=? AND class_id=?", (killian, full)).fetchall()
    conn.close()
    assert len(rows) == 1 and rows[0]["status"] == "booked"

def test_booking_failure_paths(seeded, seeded_client):
    killian = member_id(seeded_client, "Brady")
    target = class_id(seeded_client, "2026-08-05", "Strength Circuit")

    missing = book(seeded_client, 9999, target)
    assert missing.status_code == 404 and missing.get_json()["details"]["member_id"] == 9999
    assert book(seeded_client, killian, 9999).status_code == 404
    # both ids required, and must be whole numbers
    for payload in ({"member_id": killian}, {}, {"member_id": "x", "class_id": "y"}):
        assert seeded_client.post("/api/bookings", json=payload).status_code == 400
    assert seeded_client.get("/api/bookings/9999").status_code == 404
    assert seeded_client.patch("/api/bookings/9999/status", json={"status": "attended"}).status_code == 404
    assert seeded_client.delete("/api/bookings/9999").status_code == 404

    # a status outside the CHECK constraint -> 400 listing what is allowed
    existing = seeded_client.get("/api/bookings").get_json()["data"][0]
    invalid = seeded_client.patch(f"/api/bookings/{existing['id']}/status", json={"status": "maybe"})
    assert invalid.status_code == 400 and "allowed" in invalid.get_json()["details"]

    # the UNIQUE constraint backs up the application check. Defence in depth.
    conn = connect(seeded)
    conn.execute("INSERT INTO bookings (member_id, class_id, status) VALUES (?, ?, 'booked')", (killian, target))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO bookings (member_id, class_id, status) VALUES (?, ?, 'booked')", (killian, target))
    conn.close()

def test_rules_on_member_state(seeded_client):
    refused(seeded_client, member_id(seeded_client, "Behan"),           # rule 1
            class_id(seeded_client, "2026-08-04", "HIIT 45"), "Member is not active")
    detail = refused(seeded_client, member_id(seeded_client, "Byrne"),  # rule 3
                     class_id(seeded_client, "2027-03-15", "Strength Circuit"),
                     "Membership expires before this class").get_json()["details"]
    assert detail["expiry_date"] < detail["class_date"]
    assert evaluate_booking({**MEMBER, "is_active": 0}, KLASS, 0, False, [])["rule"] == 1

    # the BOUNDARY: a membership expiring ON the class date is valid, which is
    # why rule 3 uses >= rather than >
    same_day = {**KLASS, "class_date": "2026-08-15"}
    assert evaluate_booking({**MEMBER, "expiry_date": "2026-08-15"}, same_day, 0, False, []) is None
    assert evaluate_booking({**MEMBER, "expiry_date": "2026-08-14"}, same_day, 0, False, [])["rule"] == 3

def test_rules_on_class_state_and_rule_order(seeded, seeded_client):
    refused(seeded_client, member_id(seeded_client, "Brady"),           # rule 2
            class_id(seeded_client, "2026-08-12", "Spin 45"), "Class has been cancelled")

    # rule 4: Reformer Flow on 2026-08-11 is seeded 7 of 8, so one booking fills
    # it and the next is refused
    killian = member_id(seeded_client, "Brady")
    target = class_id(seeded_client, "2026-08-11", "Reformer Flow")
    assert book(seeded_client, killian, target).get_json()["data"]["spaces_left"] == 0
    assert refused(seeded_client, member_id(seeded_client, "Sheridan"), target,
                   "Class is full").get_json()["details"] == {"capacity": 8, "booked": 8}

    # RULE ORDER: Killian is now on a full class he has ALREADY booked, so rules
    # 4 and 5 both fail. Rule 4 is checked first, so that is the answer.
    refused(seeded_client, killian, target, "Class is full")

    # rule 3 likewise wins over 4 and 5, so the ordering holds across the set
    aoife = member_id(seeded_client, "Byrne")
    march = class_id(seeded_client, "2027-03-15", "Strength Circuit")
    conn = connect(seeded)
    conn.execute("UPDATE classes SET capacity=1 WHERE id=?", (march,))
    conn.execute("INSERT INTO bookings (member_id, class_id, status) VALUES (?, ?, 'booked')", (aoife, march))
    conn.commit()
    conn.close()
    refused(seeded_client, aoife, march, "Membership expires before this class")
    assert evaluate_booking(MEMBER, {**KLASS, "is_cancelled": 1}, 0, False, [])["rule"] == 2

def test_rules_on_conflicts_including_concurrency(seeded, seeded_client):
    grainne = member_id(seeded_client, "Doherty")
    hiit = class_id(seeded_client, "2026-08-04", "HIIT 45")    # Grainne is seeded on this
    spin = class_id(seeded_client, "2026-08-04", "Spin 45")    # 07:00-07:45 overlaps it
    refused(seeded_client, grainne, hiit, "Already booked")                    # rule 5
    refused(seeded_client, grainne, spin, "Clashes with an existing booking")  # rule 6

    # cancelling the blocking booking clears the clash: the same-day lookup
    # counts only active statuses
    hers = [b for b in seeded_client.get(f"/api/bookings?member_id={grainne}").get_json()["data"]
            if b["class_id"] == hiit][0]
    seeded_client.delete(f"/api/bookings/{hers['id']}")
    assert book(seeded_client, grainne, spin).status_code == 201

    # BACK-TO-BACK is not a clash: Reformer Flow 17:30-18:30 then Hyrox Prep
    # 18:30-19:30 on 2026-08-06, seeded to prove it
    killian = member_id(seeded_client, "Brady")
    for name in ("Reformer Flow", "Hyrox Prep"):
        assert book(seeded_client, killian, class_id(seeded_client, "2026-08-06", name)).status_code == 201
    assert evaluate_booking(MEMBER, KLASS, 0, True, [])["rule"] == 5
    assert evaluate_booking(MEMBER, KLASS, 0, False, [("18:30", "19:30")])["rule"] == 6
    assert evaluate_booking(MEMBER, {**KLASS, "start_time": "18:30", "end_time": "19:30"},
                            0, False, [("17:30", "18:30")]) is None

    # Concurrency: two members race for the last place. The capacity check and the
    # insert run inside one BEGIN IMMEDIATE with the count re-read under the write
    # lock; without it both threads could read "1 space left" and both insert.
    from app import create_app
    app = create_app({"TESTING": True, "DATABASE": seeded})
    with app.test_client() as setup:
        contested = class_id(setup, "2026-08-11", "Reformer Flow")   # 7 of 8
        racers = [member_id(setup, "Brady"), member_id(setup, "Sheridan")]
    results, errors, barrier = [], [], threading.Barrier(2)

    def attempt(member_pk):
        try:
            barrier.wait(timeout=5)
            with app.test_client() as racer:
                results.append(racer.post("/api/bookings",
                                          json={"member_id": member_pk, "class_id": contested}).status_code)
        except Exception as exc:                       # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=attempt, args=(m,)) for m in racers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    # whatever the timing: exactly one succeeds, and the class is never oversold
    assert not errors and sorted(results) == [201, 409], (errors, results)
    conn = connect(seeded)
    active = conn.execute("SELECT COUNT(*) AS n FROM bookings WHERE class_id=?"
                          " AND status IN ('booked','attended')", (contested,)).fetchone()["n"]
    conn.close()
    assert active == 8
