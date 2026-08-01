"""Classes CRUD. Nager.Date is stubbed to an ordinary trading
day so only the scheduling rules fire; closure paths live in test_external.py.
"""
from database import connect

def test_class_lifecycle(client, make_class, no_holidays, seeded, seeded_client):
    item = make_class()
    assert (item["capacity"], item["booked_count"], item["spaces_left"]) == (10, 0, 10)
    assert (item["is_full"], item["is_cancelled"], item["is_past"]) == (False, 0, False)
    assert client.get(f"/api/classes/{item['id']}").get_json()["data"]["bookings"] == []

    def edit(**over):
        payload = {"name": "Renamed Circuit", "instructor": "Mark Ryan",
                   "class_date": "2026-09-08", "start_time": "19:00",
                   "end_time": "20:00", "capacity": 12, "room": "Churchtown"}
        payload.update(over)
        return client.put(f"/api/classes/{item['id']}", json=payload)

    assert edit().status_code == 200
    # read again: changed in place, and a class never clashes with itself
    after = client.get(f"/api/classes/{item['id']}").get_json()["data"]
    assert after["id"] == item["id"]
    assert (after["name"], after["class_date"], after["start_time"], after["room"],
            after["spaces_left"]) == ("Renamed Circuit", "2026-09-08", "19:00", "Churchtown", 12)
    assert edit(capacity=11).status_code == 200

    # derived spaces and list filters, against the seeded data
    reformer = seeded_client.get("/api/classes?date=2026-08-11").get_json()["data"][0]
    assert (reformer["capacity"], reformer["booked_count"], reformer["spaces_left"]) == (8, 7, 1)
    detail = seeded_client.get(f"/api/classes/{reformer['id']}").get_json()["data"]
    assert len(detail["bookings"]) == 7 and detail["bookings"][0]["email"]

    # 4 Aug holds three classes, the middle one seeded to overlap for rule 6
    assert [c["start_time"] for c in seeded_client.get(
        "/api/classes?date=2026-08-04").get_json()["data"]] == ["06:30", "07:00", "18:00"]
    assert all("Laura" in c["instructor"] for c in
               seeded_client.get("/api/classes?instructor=Laura").get_json()["data"])

    # cancelled classes hidden by default; past ones excluded from upcoming
    default = seeded_client.get("/api/classes").get_json()["data"]
    assert len(seeded_client.get("/api/classes?include_cancelled=true").get_json()["data"]) == len(default) + 1
    assert all(c["is_cancelled"] == 0 for c in default)
    assert all(c["is_past"] is False for c in
               seeded_client.get("/api/classes?upcoming=true").get_json()["data"])

    # a cancelled BOOKING frees its place: the count sums only
    # booked and attended
    conn = connect(seeded)
    conn.execute("UPDATE bookings SET status='cancelled'"
                 " WHERE id=(SELECT id FROM bookings WHERE class_id=? LIMIT 1)", (reformer["id"],))
    conn.commit()
    conn.close()
    freed = seeded_client.get(f"/api/classes/{reformer['id']}").get_json()["data"]
    assert (freed["booked_count"], freed["spaces_left"]) == (6, 2)

def test_class_failure_paths(client, make_class, no_holidays, seeded, seeded_client):
    def create(**over):
        payload = {"name": "X", "instructor": "Dave O'Brien", "class_date": "2026-09-07",
                   "start_time": "18:00", "end_time": "19:00", "capacity": 10}
        payload.update(over)
        return client.post("/api/classes", json=payload)

    # end before start, and zero length, are 422 not 409
    backwards = create(start_time="19:00", end_time="18:00")
    assert backwards.status_code == 422 and "after start" in backwards.get_json()["error"]
    assert create(start_time="18:00", end_time="18:00").status_code == 422
    bad_time = create(start_time="6pm")
    assert bad_time.status_code == 400 and "HH:MM" in bad_time.get_json()["error"]

    # the same instructor overlapping on the same day -> 409
    make_class(instructor="Mark Ryan", start_time="18:00", end_time="19:00")
    clash = create(instructor="Mark Ryan", start_time="18:30", end_time="19:30")
    assert clash.status_code == 409 and "already teaching" in clash.get_json()["error"]

    # BACK-TO-BACK is not a clash: strict inequalities, via the same
    # intervals_overlap() booking rule 6 uses
    assert create(instructor="Mark Ryan", start_time="19:00", end_time="20:00").status_code == 201
    # a different instructor may overlap; the same one may on another day
    assert create(instructor="Aoife Kenny", start_time="18:30", end_time="19:30").status_code == 201
    assert create(instructor="Mark Ryan", class_date="2026-09-08").status_code == 201

    # a CANCELLED class does not block its instructor
    cancelled = make_class(instructor="Sean Daly", class_date="2026-09-09")
    assert client.patch(f"/api/classes/{cancelled['id']}/cancel").get_json()["data"]["is_cancelled"] == 1
    assert create(instructor="Sean Daly", class_date="2026-09-09").status_code == 201

    assert client.get("/api/classes/9999").status_code == 404
    assert client.put("/api/classes/9999", json={
        "name": "G", "instructor": "N", "class_date": "2026-09-07", "start_time": "10:00",
        "end_time": "11:00", "capacity": 5}).status_code == 404
    assert client.patch("/api/classes/9999/cancel").status_code == 404
    assert client.delete("/api/classes/9999").status_code == 404

    disposable = make_class(instructor="Rita Byrne", class_date="2026-09-10")
    assert client.delete(f"/api/classes/{disposable['id']}").status_code == 200
    assert client.get(f"/api/classes/{disposable['id']}").status_code == 404

    # capacity vs bookings taken, on the seeded 7-of-8 class
    seeded_item = seeded_client.get("/api/classes?date=2026-08-11").get_json()["data"][0]

    def resize(capacity):
        return seeded_client.put(f"/api/classes/{seeded_item['id']}", json={
            "name": seeded_item["name"], "instructor": seeded_item["instructor"],
            "class_date": seeded_item["class_date"], "start_time": seeded_item["start_time"],
            "end_time": seeded_item["end_time"], "room": seeded_item["room"], "capacity": capacity})

    # below the places already taken is refused; exactly equal is allowed
    too_small = resize(5)
    assert too_small.status_code == 409
    assert too_small.get_json()["details"] == {"capacity": 5, "booked": 7}
    assert resize(7).get_json()["data"]["spaces_left"] == 0

    # bookings CASCADE: a booking means nothing without its class
    assert seeded_client.delete(f"/api/classes/{seeded_item['id']}").status_code == 200
    conn = connect(seeded)
    assert conn.execute("SELECT COUNT(*) AS n FROM bookings WHERE class_id=?",
                        (seeded_item["id"],)).fetchone()["n"] == 0
    conn.close()
