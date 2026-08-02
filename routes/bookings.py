"""Booking endpoints.

    GET    /api/bookings                ?member_id= &class_id= &status=
    GET    /api/bookings/<id>
    POST   /api/bookings                the six rules, transactionally
    PATCH  /api/bookings/<id>/status
    DELETE /api/bookings/<id>           cancels, freeing the place
"""

import sqlite3

from flask import Blueprint, request

from database import get_db, row_to_dict, rows_to_list
from responses import created, err, ok
from services.booking_rules import evaluate_booking
from validators import ValidationError, as_payload, integer, text

bp = Blueprint("bookings", __name__, url_prefix="/api/bookings")

# A cancelled booking frees its place and stops counting for every rule
#. Everything below filters on these two statuses.
ACTIVE_STATUSES = ("booked", "attended")
VALID_STATUSES = ("booked", "attended", "no_show", "cancelled")


def _member(db, member_id):
    return db.execute(
        """SELECT m.*, p.name AS plan_name
             FROM members m JOIN plans p ON p.id = m.plan_id
            WHERE m.id = ?""", (member_id,)).fetchone()


def _class(db, class_id):
    return db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()


def _booked_count(db, class_id):
    return db.execute(
        "SELECT COUNT(*) AS n FROM bookings WHERE class_id = ? AND status IN (?, ?)",
        (class_id, *ACTIVE_STATUSES)).fetchone()["n"]


def _existing_booking(db, member_id, class_id):
    """The row for this member/class pair if there is one, cancelled or not.

    UNIQUE(member_id, class_id) means there can only ever be one, which is why
    a rebook has to reactivate it rather than insert a second.
    """
    return db.execute(
        "SELECT * FROM bookings WHERE member_id = ? AND class_id = ?",
        (member_id, class_id)).fetchone()


def _same_day_times(db, member_id, class_date, exclude_class_id):
    rows = db.execute(
        """SELECT c.start_time, c.end_time
             FROM bookings b JOIN classes c ON c.id = b.class_id
            WHERE b.member_id = ? AND c.class_date = ? AND c.id != ?
              AND b.status IN (?, ?) AND c.is_cancelled = 0""",
        (member_id, class_date, exclude_class_id, *ACTIVE_STATUSES)).fetchall()
    return [(r["start_time"], r["end_time"]) for r in rows]


def evaluate(db, member_id, class_id):
    """Gather the facts and run the rules.

       Returns (verdict, member_row, class_row). verdict is None when allowed.
    """
    member = _member(db, member_id)
    if member is None:
        return ({"rule": 1, "error": "Member not found", "status": 404,
                 "details": {"member_id": member_id}}, None, None)

    klass = _class(db, class_id)
    if klass is None:
        return ({"rule": 2, "error": "Class not found", "status": 404,
                 "details": {"class_id": class_id}}, member, None)

    existing = _existing_booking(db, member_id, class_id)
    verdict = evaluate_booking(
        member=dict(member),
        klass=dict(klass),
        booked_count=_booked_count(db, class_id),
        already_booked=existing is not None and existing["status"] in ACTIVE_STATUSES,
        same_day_bookings=_same_day_times(db, member_id, klass["class_date"], class_id),
    )
    return verdict, member, klass


def _detail(db, booking_id):
    return db.execute(
        """SELECT b.*, m.first_name, m.last_name, m.email,
                  c.name AS class_name, c.class_date, c.start_time, c.end_time,
                  c.room, c.capacity, c.is_cancelled AS class_cancelled
             FROM bookings b
             JOIN members m ON m.id = b.member_id
             JOIN classes c ON c.id = b.class_id
            WHERE b.id = ?""", (booking_id,)).fetchone()


# --- read -----------------------------------------------------------------

@bp.get("")
def list_bookings():
    db = get_db()
    sql = """SELECT b.*, m.first_name, m.last_name, m.email,
                    c.name AS class_name, c.class_date, c.start_time,
                    c.end_time, c.room
               FROM bookings b
               JOIN members m ON m.id = b.member_id
               JOIN classes c ON c.id = b.class_id"""
    where, params = [], []
    if request.args.get("member_id"):
        where.append("b.member_id = ?")
        params.append(request.args["member_id"])
    if request.args.get("class_id"):
        where.append("b.class_id = ?")
        params.append(request.args["class_id"])
    if request.args.get("status"):
        where.append("b.status = ?")
        params.append(request.args["status"])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.class_date DESC, c.start_time DESC, b.id"
    return ok(rows_to_list(db.execute(sql, params).fetchall()))


@bp.get("/<int:booking_id>")
def get_booking(booking_id):
    row = _detail(get_db(), booking_id)
    if row is None:
        return err("Booking not found", 404, {"id": booking_id})
    return ok(row_to_dict(row))


# --- write ----------------------------------------------------------------

@bp.post("")
def create_booking():
    payload = as_payload(request.get_json(silent=True))
    member_id = integer(payload, "member_id", minimum=1)
    class_id = integer(payload, "class_id", minimum=1)
    db = get_db()

    # First pass: cheap reads, so a refusal returns the specific rule that
    # failed without taking a write lock.
    verdict, member, klass = evaluate(db, member_id, class_id)
    if verdict:
        return err(verdict["error"], verdict["status"], verdict["details"])

    # Second pass, under a write lock. BEGIN IMMEDIATE takes SQLite's RESERVED
    # lock up front, so from here until COMMIT no other connection can write.
    # The capacity count is re-read INSIDE that lock: without this, two members
    # racing for the last place could both read "1 space left" and both insert,
    # putting the class over capacity. A second connection arriving now either
    # waits (up to the 5s connect timeout) or gets SQLITE_BUSY — it cannot
    # interleave between our count and our insert.
    db.execute("BEGIN IMMEDIATE")
    try:
        if _booked_count(db, class_id) >= klass["capacity"]:
            db.rollback()
            return err("Class is full", 409,
                       {"capacity": klass["capacity"],
                        "booked": _booked_count(db, class_id)})

        existing = _existing_booking(db, member_id, class_id)
        if existing is not None:
            if existing["status"] in ACTIVE_STATUSES:
                db.rollback()
                return err("Already booked", 409, {"class_id": class_id})
            # Cancelled, and the member wants back in. UNIQUE(member_id,
            # class_id) makes a second row impossible, so reactivate this one —
            # which also keeps one auditable row per member/class pair.
            db.execute(
                """UPDATE bookings
                      SET status = 'booked', booked_at = CURRENT_TIMESTAMP
                    WHERE id = ?""", (existing["id"],))
            booking_id = existing["id"]
            reactivated = True
        else:
            cur = db.execute(
                "INSERT INTO bookings (member_id, class_id, status) VALUES (?, ?, 'booked')",
                (member_id, class_id))
            booking_id = cur.lastrowid
            reactivated = False
        db.commit()
    except sqlite3.IntegrityError:
        # Backstop: the UNIQUE constraint enforces at database level what the
        # application already checked. Defence in depth.
        db.rollback()
        return err("Already booked", 409, {"class_id": class_id})
    except Exception:
        db.rollback()
        raise

    result = row_to_dict(_detail(db, booking_id))
    result["reactivated"] = reactivated
    result["spaces_left"] = max(0, klass["capacity"] - _booked_count(db, class_id))
    return created(result)


@bp.patch("/<int:booking_id>/status")
def update_status(booking_id):
    payload = as_payload(request.get_json(silent=True))
    status = text(payload, "status", max_len=20).lower()
    if status not in VALID_STATUSES:
        raise ValidationError(
            f"'status' must be one of {', '.join(VALID_STATUSES)}",
            {"received": status, "allowed": list(VALID_STATUSES)})

    db = get_db()
    if _detail(db, booking_id) is None:
        return err("Booking not found", 404, {"id": booking_id})
    db.execute("UPDATE bookings SET status = ? WHERE id = ?", (status, booking_id))
    db.commit()
    return ok(row_to_dict(_detail(db, booking_id)))


@bp.delete("/<int:booking_id>")
def cancel_booking(booking_id):
    """Cancelling sets the status rather than deleting the row.

    The place is freed immediately because every count filters on
    status IN ('booked','attended'), and the surviving row is what lets the
    member rebook later by reactivation.
    """
    db = get_db()
    row = _detail(db, booking_id)
    if row is None:
        return err("Booking not found", 404, {"id": booking_id})

    db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
    db.commit()

    result = row_to_dict(_detail(db, booking_id))
    result["spaces_left"] = max(
        0, row["capacity"] - _booked_count(db, row["class_id"]))
    return ok(result)
