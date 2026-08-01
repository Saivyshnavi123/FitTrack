"""Plans CRUD: one lifecycle test, one failure-path test."""

def test_plan_lifecycle(client, make_plan):
    created = client.post("/api/plans", json={
        "name": "Unlimited Monthly", "price_eur": 89.0, "duration_days": 30,
        "description": "All classes, both studios."})
    assert created.status_code == 201
    plan = created.get_json()["data"]
    assert (plan["name"], plan["price_eur"], plan["duration_days"]) \
        == ("Unlimited Monthly", 89.0, 30)

    # read back
    assert client.get(f"/api/plans/{plan['id']}").get_json()["data"] == plan

    # list: live member count, ordered cheapest first
    make_plan(name="Off-Peak Monthly", price_eur=49.0)
    listed = client.get("/api/plans").get_json()["data"]
    assert [p["name"] for p in listed] == ["Off-Peak Monthly", "Unlimited Monthly"]
    assert all(p["member_count"] == 0 for p in listed)

    # update every field
    updated = client.put(f"/api/plans/{plan['id']}", json={
        "name": "Annual Unlimited", "price_eur": 799.0, "duration_days": 365,
        "description": "Twelve months."})
    assert updated.status_code == 200

    # read again: changed in place, same row
    after = client.get(f"/api/plans/{plan['id']}").get_json()["data"]
    assert after["id"] == plan["id"]
    assert (after["name"], after["price_eur"], after["duration_days"],
            after["description"]) == ("Annual Unlimited", 799.0, 365,
                                      "Twelve months.")

def test_plan_failure_paths(client, db, make_plan):
    make_plan(name="Off-Peak Monthly")

    # duplicate name -> 409 (UNIQUE(name))
    duplicate = client.post("/api/plans", json={
        "name": "Off-Peak Monthly", "price_eur": 49.0, "duration_days": 30})
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.get_json()["error"]

    # validation mirrors the CHECK constraints, with a readable message
    assert client.post("/api/plans", json={
        "name": "Negative", "price_eur": -1, "duration_days": 30}).status_code == 400
    assert client.post("/api/plans", json={
        "name": "Zero Days", "price_eur": 10, "duration_days": 0}).status_code == 400
    missing = client.post("/api/plans", json={"price_eur": 10, "duration_days": 30})
    assert missing.status_code == 400
    assert "required" in missing.get_json()["error"]

    # not found on every verb
    assert client.get("/api/plans/9999").status_code == 404
    assert client.put("/api/plans/9999", json={"name": "G", "price_eur": 1,
                                               "duration_days": 1}).status_code == 404
    assert client.delete("/api/plans/9999").status_code == 404

    # renaming onto a name another plan holds -> 409
    mine = make_plan(name="Mine")
    assert client.put(f"/api/plans/{mine['id']}", json={
        "name": "Off-Peak Monthly", "price_eur": 10,
        "duration_days": 30}).status_code == 409

    # an unused plan deletes and is gone
    assert client.delete(f"/api/plans/{mine['id']}").status_code == 200
    assert client.get(f"/api/plans/{mine['id']}").status_code == 404

    # a plan with members on it cannot be deleted. ON DELETE RESTRICT in the
    # schema AND an application check, so the response can explain why.
    in_use = make_plan(name="In Use")
    db.execute("""INSERT INTO members (first_name, last_name, email, plan_id,
                                       start_date, expiry_date)
                  VALUES ('Aoife', 'Byrne', 'a.b@example.com', ?,
                          '2026-07-15', '2026-08-14')""", (in_use["id"],))
    db.commit()
    blocked = client.delete(f"/api/plans/{in_use['id']}")
    assert blocked.status_code == 409
    assert blocked.get_json()["details"]["members"] == 1
    assert client.get(f"/api/plans/{in_use['id']}").status_code == 200
