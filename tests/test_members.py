"""Members CRUD: lifecycle, then failure paths."""
from datetime import date

from database import connect

def _today():
    return date.today().strftime("%Y-%m-%d")

def test_member_lifecycle(client, make_plan, make_member, seeded_client):
    monthly = make_plan(name="Monthly", duration_days=30)
    annual = make_plan(name="Annual", duration_days=365)

    def edit(member_id, **over):
        payload = {"first_name": "Aoife", "last_name": "Byrne", "email": "aoife.byrne@example.com",
                   "plan_id": annual["id"], "start_date": "2026-09-01"}
        payload.update(over)
        return client.put(f"/api/members/{member_id}", json=payload).get_json()["data"]

    # create: expiry = start_date + plan.duration_days, calculated server-side
    # and STORED, so editing the plan later cannot move it
    created = client.post("/api/members", json={
        "first_name": "Aoife", "last_name": "Byrne", "email": "aoife.byrne@example.com",
        "phone": "085 123 4401", "plan_id": monthly["id"], "start_date": "2026-07-15"})
    assert created.status_code == 201
    member = created.get_json()["data"]
    assert (member["expiry_date"], member["plan_name"], member["status"]) == ("2026-08-14", "Monthly", "active")

    # read back, with plan detail and an empty booking history
    fetched = client.get(f"/api/members/{member['id']}").get_json()["data"]
    assert fetched["plan_name"] == "Monthly" and fetched["bookings"] == []

    # inactive is independent of expiry
    inactive = client.post("/api/members", json={
        "first_name": "Ruth", "last_name": "Behan", "email": "ruth@example.com",
        "plan_id": annual["id"], "start_date": _today(), "is_active": False}).get_json()["data"]
    assert (inactive["is_expired"], inactive["status"]) == (False, "inactive")

    # update: changing start date OR plan recalculates expiry
    assert edit(member["id"], plan_id=monthly["id"])["expiry_date"] == "2026-10-01"
    assert edit(member["id"])["expiry_date"] == "2027-09-01"

    # renew extends from the CURRENT expiry while live, so paid days are not lost.
    # 2027-09-01 + 365 lands on 2028-08-31: 29 Feb 2028 is inside that span.
    renewed = client.post(f"/api/members/{member['id']}/renew").get_json()["data"]
    assert (renewed["renewed_from"], renewed["expiry_date"]) == ("2027-09-01", "2028-08-31")

    # editing something else must not silently wipe out that renewal
    assert edit(member["id"], phone="086 999 0000")["expiry_date"] == "2028-08-31"

    # read again: every change persisted on the same row
    after = client.get(f"/api/members/{member['id']}").get_json()["data"]
    assert after["id"] == member["id"]
    assert (after["plan_name"], after["start_date"], after["expiry_date"], after["phone"]) \
        == ("Annual", "2026-09-01", "2028-08-31", "086 999 0000")

    # renewing an EXPIRED membership runs from today, else it stays expired
    lapsed = make_member(email="lapsed@example.com", plan_id=monthly["id"], start_date="2020-01-01")
    assert lapsed["is_expired"] is True
    revived = client.post(f"/api/members/{lapsed['id']}/renew").get_json()["data"]
    assert (revived["renewed_from"], revived["is_expired"]) == (_today(), False)

    # list, against the seeded data: ordered by surname, tiebroken by id
    everyone = seeded_client.get("/api/members").get_json()["data"]
    assert len(everyone) == 13
    assert [m["last_name"] for m in everyone] == sorted(m["last_name"] for m in everyone)

    # expired and inactive are flagged: the shading and the rules rely on it
    status = {m["last_name"]: m["status"] for m in everyone}
    assert (status["Doyle"], status["Behan"], status["Brady"]) == ("expired", "inactive", "active")

    # filter by plan
    taster = next(p for p in seeded_client.get("/api/plans").get_json()["data"]
                  if p["name"] == "Taster Pack")
    assert [m["last_name"] for m in seeded_client.get(
        f"/api/members?plan_id={taster['id']}").get_json()["data"]] == ["Farrell"]

def test_member_failure_paths(client, make_plan, make_member, seeded, seeded_client):
    plan = make_plan()
    existing = make_member(email="dup@example.com", plan_id=plan["id"])

    def create(**over):
        payload = {"first_name": "X", "last_name": "Y", "email": "x.y@example.com",
                   "plan_id": plan["id"], "start_date": "2026-07-15"}
        payload.update(over)
        return client.post("/api/members", json=payload)

    assert create(email="dup@example.com").status_code == 409   # UNIQUE(email)
    # unknown plan -> 422: invalid input, not a missing resource, because the
    # MEMBER is what is being addressed
    unknown = create(plan_id=9999)
    assert unknown.status_code == 422 and unknown.get_json()["details"]["plan_id"] == 9999
    assert create(email="not-an-email").status_code == 400
    bad_date = create(start_date="15/07/2026")
    assert bad_date.status_code == 400 and "YYYY-MM-DD" in bad_date.get_json()["error"]

    assert client.get("/api/members/9999").status_code == 404
    assert client.put("/api/members/9999", json={
        "first_name": "A", "last_name": "B", "email": "a.b@example.com",
        "plan_id": plan["id"], "start_date": "2026-07-15"}).status_code == 404
    assert client.post("/api/members/9999/renew").status_code == 404
    assert client.delete("/api/members/9999").status_code == 404

    # taking an email another member holds -> 409
    mine = make_member(email="mine@example.com", plan_id=plan["id"])
    assert client.put(f"/api/members/{mine['id']}", json={
        "first_name": "M", "last_name": "M", "email": existing["email"],
        "plan_id": plan["id"], "start_date": "2026-07-15"}).status_code == 409

    assert client.delete(f"/api/members/{mine['id']}").status_code == 200
    assert client.get(f"/api/members/{mine['id']}").status_code == 404

    # bookings CASCADE: a booking has no meaning without its member
    conn = connect(seeded)
    seeded_id = conn.execute("SELECT id FROM members WHERE email='aoife.byrne@example.com'").fetchone()["id"]
    assert conn.execute("SELECT COUNT(*) AS n FROM bookings WHERE member_id=?", (seeded_id,)).fetchone()["n"] > 0
    conn.close()
    assert seeded_client.delete(f"/api/members/{seeded_id}").status_code == 200
    conn = connect(seeded)
    assert conn.execute("SELECT COUNT(*) AS n FROM bookings WHERE member_id=?", (seeded_id,)).fetchone()["n"] == 0
    conn.close()
