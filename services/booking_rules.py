"""Pure business logic — no database, no Flask, no network.

Everything here takes plain values and returns plain values, so it is unit
testable in isolation: membership expiry, the six booking rules, and the
interval overlap used for both instructor clashes and double-booking.
"""

from datetime import date, datetime, timedelta

DATE_FMT = "%Y-%m-%d"


def parse_date(value):
    """Accept a date object or a YYYY-MM-DD string; return a date."""
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), DATE_FMT).date()


def calculate_expiry(start_date, duration_days):
    """expiry_date = start_date + plan.duration_days

    Stored on the member rather than derived on read, so that editing a plan's
    duration later does not retroactively move existing members' expiry dates.
    """
    return (parse_date(start_date) + timedelta(days=int(duration_days))).strftime(DATE_FMT)


def intervals_overlap(start_a, end_a, start_b, end_b):
    """Do two time intervals on the same day overlap?

        overlap  <=>  a.start < b.end  AND  a.end > b.start

    STRICT inequalities, so back-to-back intervals do NOT overlap: a class
    ending at 18:30 and another starting at 18:30 are fine, which is exactly
    how a real timetable runs.

    Times are "HH:MM" strings. Zero-padded 24-hour text compares
    lexicographically in the same order it compares chronologically, so no
    parsing is needed — and the same expression works unchanged inside SQL.

    ONE implementation, TWO callers: the instructor scheduling check in
    routes/classes.py and booking rule 6 in this module. Keeping it here stops
    the two from drifting apart.
    """
    return start_a < end_b and end_a > start_b


def is_expired(expiry_date, today=None):
    """Expired strictly *after* the expiry date — a membership is valid on its
       own expiry date, which is the boundary case the tests pin down."""
    today = parse_date(today) if today else date.today()
    return parse_date(expiry_date) < today


# --- the six booking rules -----------------------------
#
# Pure. Every input is a plain value the caller has already read from the
# database, so the whole rule set is testable with no database and no Flask.
#
# Checked IN ORDER, and the FIRST failure is returned. That ordering is part of
# the specification, not an implementation detail: an expired member trying to
# rebook a full class must be told their membership expired, because that is
# the problem they have to fix first.

RULES = (
    (1, "Member is not active"),
    (2, "Class has been cancelled"),
    (3, "Membership expires before this class"),
    (4, "Class is full"),
    (5, "Already booked"),
    (6, "Clashes with an existing booking"),
)


def _fail(rule, message, status, details=None):
    return {"rule": rule, "error": message, "status": status,
            "details": details or {}}


def evaluate_booking(member, klass, booked_count, already_booked,
                     same_day_bookings):
    """Run all six rules in order. Return None if the booking may proceed,
       otherwise the first failure.

    member  -- dict with is_active and expiry_date
    klass   -- dict with is_cancelled, class_date, start_time, end_time, capacity
    booked_count       -- places already taken (booked + attended only)
    already_booked     -- True if this member holds a NON-cancelled booking here
    same_day_bookings  -- [(start_time, end_time)] for this member, same date,
                          excluding this class and excluding cancelled bookings
    """
    # 1 -- member exists and is active
    if not member.get("is_active"):
        return _fail(1, "Member is not active", 409,
                     {"member_id": member.get("id")})

    # 2 -- class exists and is not cancelled
    if klass.get("is_cancelled"):
        return _fail(2, "Class has been cancelled", 409,
                     {"class_id": klass.get("id")})

    # 3 -- membership must still be valid ON the day of the class.
    #      >= not >, so a membership expiring on the class date is fine.
    if member["expiry_date"] < klass["class_date"]:
        return _fail(3, "Membership expires before this class", 409,
                     {"expiry_date": member["expiry_date"],
                      "class_date": klass["class_date"]})

    # 4 -- capacity. Cancelled bookings have already freed their place.
    if booked_count >= klass["capacity"]:
        return _fail(4, "Class is full", 409,
                     {"capacity": klass["capacity"], "booked": booked_count})

    # 5 -- not already booked. A CANCELLED booking does not count, so a member
    #      who cancels may rebook (the existing row is reactivated).
    if already_booked:
        return _fail(5, "Already booked", 409, {"class_id": klass.get("id")})

    # 6 -- no overlap with another booking that day. Same intervals_overlap()
    #      the instructor scheduling check uses, so the two cannot drift.
    for start, end in same_day_bookings:
        if intervals_overlap(klass["start_time"], klass["end_time"], start, end):
            return _fail(6, "Clashes with an existing booking", 409,
                         {"class_time": f"{klass['start_time']}-{klass['end_time']}",
                          "clashes_with": f"{start}-{end}"})

    return None
