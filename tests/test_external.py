"""Nager.Date integration, mocked against the REAL captured
payload. The pure classification rule is unit tested in test_rules.py.
"""
from unittest.mock import patch

import requests

from conftest import load_fixture
from database import connect
from services.holiday_service import (cache_year, cached_year, check_closure,
                                      year_is_cached)

CONFIG = {"COUNTRY_CODE": "IE", "NAGER_BASE": "https://date.nager.at/api/v3",
          "HTTP_TIMEOUT": 10, "USER_AGENT": "FitTrack-Student-Project/1.0"}

def post_class(client, day, instructor="Dave O'Brien", start="18:00", end="19:00"):
    return client.post("/api/classes", json={
        "name": "Strength Circuit", "instructor": instructor, "class_date": day,
        "start_time": start, "end_time": end, "capacity": 10, "room": "Knocklyon"})

def test_public_holiday_blocks_class_creation(client, db_path, nager):
    resp = post_class(client, "2027-03-17")            # St Patrick's Day
    assert resp.status_code == 409 and "closed" in resp.get_json()["error"]
    details = resp.get_json()["details"]
    assert (details["holiday"], details["types"]) == ("Saint Patrick's Day", ["Public"])

    # nothing written, but the year was fetched and cached with is_closure
    # computed on insert, so every later check is a local lookup
    conn = connect(db_path)
    assert conn.execute("SELECT COUNT(*) AS n FROM classes").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM holiday_cache").fetchone()["n"] == 11
    conn.close()

    data = client.get("/api/holidays/2027").get_json()["data"]
    assert (data["year"], data["country_code"], len(data["holidays"]),
            data["closures"]) == (2027, "IE", 11, 10)   # 10 closures: all but Good Friday

def test_good_friday_is_allowed_with_an_advisory(client, nager):
    resp = post_class(client, "2027-03-26", "Mark Ryan", "18:30", "19:30")
    assert resp.status_code == 201                     # the gym trades that day
    data = resp.get_json()["data"]
    assert "Good Friday" in data["advisory"] and data["warning"] is None
    # everything app.js reads when rendering the amber advisory box
    check = data["holiday_check"]
    assert (check["is_closure"], check["types"], check["holiday_name"], check["local_name"]) \
        == (False, ["Bank", "School"], "Good Friday", "Aoine an Chéasta")

    # an ORDINARY day gets neither an advisory nor a warning
    plain = post_class(client, "2027-03-16", "Laura F", "07:00", "07:45").get_json()["data"]
    assert plain["advisory"] is None and plain["holiday_check"]["holiday_name"] is None

def test_failure_paths_cache_hit_and_empty_cache_fallback(client, db_path):
    payload = load_fixture("nager_ie_2027.json")
    # three classes in one year cause exactly ONE fetch
    with patch("services.holiday_service._session.get") as mock_get:
        mock_get.return_value.json.return_value = payload
        mock_get.return_value.raise_for_status.return_value = None
        for day in ("2027-03-15", "2027-03-16", "2027-03-18"):
            assert post_class(client, day, f"I {day}").status_code == 201
        assert mock_get.call_count == 1

    # the cached rows carry is_closure, and the Irish local_name survives the
    # SQLite round trip intact
    conn = connect(db_path)
    rows = {r["date"]: r for r in cached_year(conn, 2027)}
    conn.close()
    assert (rows["2027-03-17"]["is_closure"], rows["2027-03-26"]["is_closure"]) == (True, False)
    assert (rows["2027-03-17"]["local_name"], rows["2027-03-26"]["local_name"]) \
        == ("Lá Fhéile Pádraig", "Aoine an Chéasta")

    # PATH 3: with the year cached, an unreachable API is never even called
    with patch("services.holiday_service._session.get",
               side_effect=requests.ConnectionError("down")) as unreachable:
        blocked = post_class(client, "2027-03-17", "Someone New")
        assert unreachable.call_count == 0
    assert blocked.status_code == 409

    # a year that is NOT cached triggers its own fetch rather than riding
    # another year's cache
    conn = connect(db_path)
    cache_year(conn, 2026, load_fixture("nager_ie_2026.json"), "IE")
    conn.execute("DELETE FROM holiday_cache WHERE substr(holiday_date,1,4)='2027'")
    conn.commit()
    assert year_is_cached(conn, 2026) is True and year_is_cached(conn, 2027) is False
    conn.close()
    with patch("services.holiday_service._session.get") as refetch:
        refetch.return_value.json.return_value = payload
        refetch.return_value.raise_for_status.return_value = None
        again = post_class(client, "2027-03-17", "Someone Else")
        assert refetch.call_count == 1 and "2027" in refetch.call_args[0][0]
    assert again.status_code == 409                 # blocked, not waved through

    # PATH 4: API down and nothing cached. A deliberate trade-off -- an unusable
    # scheduling screen is worse than a class on a closed day, which a human can
    # still spot.
    with patch("services.holiday_service._session.get",
               side_effect=requests.ConnectionError("down")):
        allowed = post_class(client, "2029-03-17", "Nora Quinn")
        assert allowed.status_code == 201
        data = allowed.get_json()["data"]
        assert "unavailable" in data["warning"] and data["advisory"] is None
        assert data["holiday_check"]["source"] == "unavailable"
        assert client.get("/api/holidays/2030").status_code == 503

    # check_closure never RAISES, even on a malformed response body
    conn = connect(db_path)
    with patch("services.holiday_service._session.get") as bad_body:
        bad_body.return_value.json.side_effect = ValueError("not json")
        bad_body.return_value.raise_for_status.return_value = None
        result = check_closure(conn, "2031-03-17", CONFIG)
    conn.close()
    assert result["is_closure"] is False and result["warning"] is not None
