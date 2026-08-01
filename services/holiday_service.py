"""Nager.Date integration — gym closure days.

The rule is NOT "is this date a holiday". Live data shows Good Friday returns
types ["Bank","School"] in Ireland while every other entry returns ["Public"].
Banks and schools close on Good Friday; a gym trades. So:

    is_closure = "Public" in holiday.types

A Bank/School-only day is cached with is_closure = 0, the class is allowed, and
the response carries an advisory so the manager can decide. Blocking on any
holiday would have wrongly closed FIREFIT on a day it opens.

Called from Flask, never the browser, so the scheduling rule cannot
be bypassed by editing JavaScript, and results can be cached into SQLite.

Encoding note: localName values are Irish — "Lá Fhéile Pádraig", "Aoine an
Chéasta". They round-trip fine through SQLite and JSON, which are both UTF-8,
but must never be printed to a cp1252 Windows console.
"""

from datetime import datetime

import requests

# One Session for this service. Sets the shared headers once, reuses the
# connection, and gives the module its own patch point: tests stub
# services.holiday_service._session.get rather than the shared requests module.
_session = requests.Session()


# --- pure -----------------------------------------------------------------

def classify_closure(types):
    """The whole closure rule, as one testable expression."""
    return "Public" in (types or [])


def parse_types(raw):
    """holiday_cache stores types as CSV; the API sends a list."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return [t for t in str(raw).split(",") if t]


def advisory_for(name, types):
    """Text shown when a date is a holiday but NOT a closure."""
    kinds = ", ".join(parse_types(types))
    return (f"{name} falls on this date (types: {kinds}). "
            f"That does not include 'Public', so the gym trades as normal and "
            f"this class is allowed.")


# --- network --------------------------------------------------------------

def fetch_year(year, country_code, base_url, timeout=10, user_agent=None):
    """GET /api/v3/PublicHolidays/{year}/{country}. Returns a BARE ARRAY."""
    url = f"{base_url}/PublicHolidays/{year}/{country_code}"
    headers = {"User-Agent": user_agent} if user_agent else {}
    response = _session.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


# --- cache ----------------------------------------------------------------

def year_is_cached(db, year):
    row = db.execute(
        "SELECT COUNT(*) AS n FROM holiday_cache WHERE substr(holiday_date, 1, 4) = ?",
        (str(year),),
    ).fetchone()
    return row["n"] > 0


def cache_year(db, year, holidays, country_code):
    """Compute is_closure once, on insert, so every later check is a local lookup."""
    fetched_at = datetime.now().isoformat(timespec="seconds")
    for holiday in holidays:
        types = holiday.get("types") or []
        db.execute(
            """INSERT OR REPLACE INTO holiday_cache
                   (holiday_date, name, local_name, types, is_closure,
                    country_code, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (holiday["date"], holiday.get("name"), holiday.get("localName"),
             ",".join(types), 1 if classify_closure(types) else 0,
             country_code, fetched_at),
        )
    db.commit()
    return len(holidays)


def cached_year(db, year):
    rows = db.execute(
        """SELECT * FROM holiday_cache
            WHERE substr(holiday_date, 1, 4) = ?
            ORDER BY holiday_date""",
        (str(year),),
    ).fetchall()
    return [
        {
            "date": r["holiday_date"],
            "name": r["name"],
            "local_name": r["local_name"],
            "types": parse_types(r["types"]),
            "is_closure": bool(r["is_closure"]),
            "country_code": r["country_code"],
            "fetched_at": r["fetched_at"],
        }
        for r in rows
    ]


# --- orchestration --------------------------------------------------------

def ensure_year(db, year, config):
    """Make sure `year` is in the cache, fetching it if not.

    A class scheduled into a year nobody has looked at yet triggers a fetch for
    that year specifically — it neither fails nor silently passes.

    Returns "cache" (already had it), "api" (fetched now) or "unavailable"
    (fetch failed and we have nothing for that year).

    Caveat worth knowing: emptiness is how we detect "not cached", so a country
    and year with genuinely zero holidays would be re-fetched every time.
    Ireland returns 11 a year, so this never bites here.
    """
    if year_is_cached(db, year):
        return "cache"
    try:
        holidays = fetch_year(
            year,
            config["COUNTRY_CODE"],
            config["NAGER_BASE"],
            config.get("HTTP_TIMEOUT", 10),
            config.get("USER_AGENT"),
        )
    except (requests.RequestException, ValueError):
        return "unavailable"
    cache_year(db, year, holidays, config["COUNTRY_CODE"])
    return "api"


def check_closure(db, class_date, config):
    """Decide whether a class may be scheduled on `class_date`.

    Never raises. The four paths, all tested:
      1. "Public" holiday        -> is_closure True, the caller blocks with 409
      2. Bank/School-only day    -> is_closure False + advisory, class allowed
      3. API down, year cached   -> answered from cache
      4. API down, year missing  -> is_closure False + warning, class allowed

    Path 4 is a deliberate trade-off: an unusable scheduling screen is worse
    than a class booked on a closed day, which a human can still spot.
    """
    year = str(class_date)[:4]
    source = ensure_year(db, year, config)

    result = {
        "date": class_date,
        "is_closure": False,
        "holiday_name": None,
        "local_name": None,
        "types": [],
        "advisory": None,
        "warning": None,
        "source": source,
    }

    if source == "unavailable":
        result["warning"] = (
            f"Public holiday data for {year} is unavailable and nothing is "
            f"cached for that year, so this date could not be checked against "
            f"the closure calendar. The class was allowed."
        )
        return result

    row = db.execute(
        "SELECT * FROM holiday_cache WHERE holiday_date = ?", (class_date,)
    ).fetchone()
    if row is None:
        return result                      # an ordinary trading day

    result["holiday_name"] = row["name"]
    result["local_name"] = row["local_name"]
    result["types"] = parse_types(row["types"])
    result["is_closure"] = bool(row["is_closure"])
    if not result["is_closure"]:
        result["advisory"] = advisory_for(row["name"], result["types"])
    return result
