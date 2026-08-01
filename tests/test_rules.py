"""Business rules in isolation: interval overlap, expiry, and holiday
closure classification. No network — Nager.Date data comes from the
captured fixtures."""
from conftest import load_fixture
from database import connect
from services.booking_rules import (calculate_expiry, evaluate_booking,
                                    intervals_overlap, is_expired)
from services.holiday_service import advisory_for, classify_closure, parse_types

def test_intervals_overlap_at_every_boundary():
    # ONE implementation, TWO callers: the instructor scheduling check and
    # booking rule 6. Back-to-back must NOT overlap, in either direction --
    # that is why the rule uses strict inequalities.
    assert intervals_overlap("17:30", "18:30", "18:30", "19:30") is False
    assert intervals_overlap("18:30", "19:30", "17:30", "18:30") is False
    assert intervals_overlap("09:00", "10:00", "09:00", "10:00") is True   # identical
    assert intervals_overlap("09:00", "10:00", "09:30", "10:30") is True   # partial
    assert intervals_overlap("09:30", "10:30", "09:00", "10:00") is True   # partial
    assert intervals_overlap("09:00", "11:00", "09:30", "10:00") is True   # contains
    assert intervals_overlap("09:30", "10:00", "09:00", "11:00") is True   # contained
    assert intervals_overlap("09:00", "10:00", "09:59", "10:59") is True   # one minute
    assert intervals_overlap("09:00", "10:00", "10:01", "11:00") is False  # one gap
    assert intervals_overlap("09:00", "10:00", "14:00", "15:00") is False  # apart
    assert intervals_overlap("14:00", "15:00", "09:00", "10:00") is False  # apart
    assert intervals_overlap("06:30", "07:15", "07:00", "07:45") is True   # seeded pair
    # symmetric: swapping the intervals never changes the answer
    for a, b, c, d in [("09:00", "10:00", "09:30", "10:30"),
                       ("17:30", "18:30", "18:30", "19:30"),
                       ("09:00", "10:00", "14:00", "15:00")]:
        assert intervals_overlap(a, b, c, d) is intervals_overlap(c, d, a, b)

def test_calculate_expiry_and_the_expiry_boundary():
    assert calculate_expiry("2026-07-15", 30) == "2026-08-14"
    assert calculate_expiry("2026-02-01", 365) == "2027-02-01"
    assert calculate_expiry("2026-12-15", 30) == "2027-01-14"    # year boundary
    assert calculate_expiry("2028-02-01", 30) == "2028-03-02"    # leap day
    # a membership is VALID ON its expiry date -- expiry is the last valid day,
    # not the first invalid one, which is why booking rule 3 uses >= not >
    assert is_expired("2026-08-14", today="2026-08-14") is False
    assert is_expired("2026-08-14", today="2026-08-15") is True

def test_closure_classification_and_spaces_remaining(seeded, seeded_client):
    # The rule is `"Public" in types`, NOT "is it a holiday". Good Friday is
    # Bank/School only in Ireland: banks and schools close, a gym trades.
    # Blocking on any holiday would have closed FIREFIT on a trading day.
    for year in ("2026", "2027"):
        fixture = load_fixture(f"nager_ie_{year}.json")
        assert len(fixture) == 11
        paddys = next(h for h in fixture if "Patrick" in h["name"])
        good_friday = next(h for h in fixture if "Good Friday" in h["name"])
        assert (paddys["date"][5:], paddys["types"]) == ("03-17", ["Public"])
        assert classify_closure(paddys["types"]) is True
        assert good_friday["types"] == ["Bank", "School"]
        assert classify_closure(good_friday["types"]) is False
        # Good Friday is the ONLY non-Public entry, in both years
        assert [h["name"] for h in fixture
                if not classify_closure(h["types"])] == ["Good Friday"]
    assert next(h for h in load_fixture("nager_ie_2027.json")
                if "Good Friday" in h["name"])["date"] == "2027-03-26"

    assert classify_closure([]) is False and classify_closure(None) is False
    assert classify_closure(["Bank"]) is False
    assert classify_closure(["Public", "Bank"]) is True
    # types round-trip between the API's list and the cache's CSV
    assert parse_types("Bank,School") == ["Bank", "School"]
    assert parse_types(["Public"]) == ["Public"]
    assert parse_types("") == [] and parse_types(None) == []
    advisory = advisory_for("Good Friday", ["Bank", "School"])
    assert all(s in advisory for s in
               ("Good Friday", "Bank, School", "Public", "allowed"))

    # spaces remaining excludes cancelled, but not attended
    item = seeded_client.get("/api/classes?date=2026-08-11").get_json()["data"][0]
    assert (item["capacity"], item["booked_count"], item["spaces_left"]) == (8, 7, 1)

    def set_status(booking_id, status):
        conn = connect(seeded)
        conn.execute("UPDATE bookings SET status=? WHERE id=?", (status, booking_id))
        conn.commit()
        conn.close()
        data = seeded_client.get(f"/api/classes/{item['id']}").get_json()["data"]
        return data["booked_count"], data["spaces_left"]

    conn = connect(seeded)
    booking_id = conn.execute("SELECT id FROM bookings WHERE class_id=? LIMIT 1",
                              (item["id"],)).fetchone()["id"]
    conn.close()
    assert set_status(booking_id, "cancelled") == (6, 2)
    assert set_status(booking_id, "attended") == (7, 1)

    # the pure rule agrees: the last place is bookable, one more is not
    member = {"id": 1, "is_active": 1, "expiry_date": "2099-01-01"}
    klass = {"id": 2, "is_cancelled": 0, "class_date": "2099-01-01",
             "start_time": "18:00", "end_time": "19:00", "capacity": 8}
    assert evaluate_booking(member, klass, 7, False, []) is None
    assert evaluate_booking(member, klass, 8, False, [])["rule"] == 4
