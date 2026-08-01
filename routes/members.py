"""Member endpoints.

    GET    /api/members            ?plan_id=
    GET    /api/members/<id>       includes plan and booking history
    POST   /api/members            calculates expiry_date
    PUT    /api/members/<id>       recalculates expiry if plan or start date changes
    DELETE /api/members/<id>
    POST   /api/members/<id>/renew extends expiry by the plan duration
"""

import sqlite3
from datetime import date

from flask import Blueprint, request

from database import get_db, rows_to_list
from responses import created, err, ok
from services.booking_rules import calculate_expiry, parse_date
from validators import validate_member

bp = Blueprint("members", __name__, url_prefix="/api/members")

BASE_SELECT = """
    SELECT m.*, p.name AS plan_name, p.price_eur AS plan_price,
           p.duration_days AS plan_duration_days
      FROM members m
      JOIN plans p ON p.id = m.plan_id
"""


def _decorate(row, today=None):
    """Add the derived fields the UI needs. expiry_date itself stays stored,
       not computed."""
    today = today or date.today().strftime("%Y-%m-%d")
    member = dict(row)
    member["is_expired"] = member["expiry_date"] < today
    member["days_remaining"] = (
        parse_date(member["expiry_date"]) - parse_date(today)
    ).days
    if not member["is_active"]:
        member["status"] = "inactive"
    elif member["is_expired"]:
        member["status"] = "expired"
    else:
        member["status"] = "active"
    return member


def _fetch(db, member_id):
    return db.execute(BASE_SELECT + " WHERE m.id = ?", (member_id,)).fetchone()


def _plan(db, plan_id):
    return db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()


@bp.get("")
def list_members():
    db = get_db()
    today = date.today().strftime("%Y-%m-%d")

    sql = BASE_SELECT
    params = []

    plan_id = request.args.get("plan_id")
    if plan_id:
        sql += " WHERE m.plan_id = ?"
        params.append(plan_id)

    # Ordered by surname, with the primary key as a tiebreaker so members
    # sharing a surname do not reshuffle between identical requests.
    sql += " ORDER BY m.last_name ASC, m.id ASC"

    rows = db.execute(sql, params).fetchall()
    return ok([_decorate(r, today) for r in rows])


@bp.get("/<int:member_id>")
def get_member(member_id):
    db = get_db()
    row = _fetch(db, member_id)
    if row is None:
        return err("Member not found", 404, {"id": member_id})

    member = _decorate(row)
    member["bookings"] = rows_to_list(db.execute(
        """SELECT b.id, b.status, b.booked_at,
                  c.id AS class_id, c.name AS class_name, c.class_date,
                  c.start_time, c.end_time, c.room, c.is_cancelled
             FROM bookings b
             JOIN classes c ON c.id = b.class_id
            WHERE b.member_id = ?
            ORDER BY c.class_date DESC, c.start_time DESC""",
        (member_id,),
    ).fetchall())
    return ok(member)


@bp.post("")
def create_member():
    fields = validate_member(request.get_json(silent=True))
    db = get_db()

    plan = _plan(db, fields["plan_id"])
    if plan is None:
        return err("Plan not found", 422, {"plan_id": fields["plan_id"]})

    # Calculated once, at creation, and stored.
    expiry = calculate_expiry(fields["start_date"], plan["duration_days"])

    try:
        cur = db.execute(
            """INSERT INTO members (first_name, last_name, email, phone,
                                    plan_id, start_date, expiry_date, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (fields["first_name"], fields["last_name"], fields["email"],
             fields["phone"], fields["plan_id"], fields["start_date"], expiry,
             1 if fields["is_active"] else 0),
        )
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        if "UNIQUE" in str(exc):
            return err("A member with that email already exists", 409,
                       {"email": fields["email"]})
        return err("Member could not be created", 400, {"reason": str(exc)})

    return created(_decorate(_fetch(db, cur.lastrowid)))


@bp.put("/<int:member_id>")
def update_member(member_id):
    db = get_db()
    existing = _fetch(db, member_id)
    if existing is None:
        return err("Member not found", 404, {"id": member_id})

    fields = validate_member(request.get_json(silent=True))
    plan = _plan(db, fields["plan_id"])
    if plan is None:
        return err("Plan not found", 422, {"plan_id": fields["plan_id"]})

    # Recalculate only when the inputs to the calculation changed. Otherwise the
    # stored expiry is left alone, including any extension added by /renew.
    if (fields["plan_id"] != existing["plan_id"]
            or fields["start_date"] != existing["start_date"]):
        expiry = calculate_expiry(fields["start_date"], plan["duration_days"])
    else:
        expiry = existing["expiry_date"]

    try:
        db.execute(
            """UPDATE members
                  SET first_name = ?, last_name = ?, email = ?, phone = ?,
                      plan_id = ?, start_date = ?, expiry_date = ?, is_active = ?
                WHERE id = ?""",
            (fields["first_name"], fields["last_name"], fields["email"],
             fields["phone"], fields["plan_id"], fields["start_date"], expiry,
             1 if fields["is_active"] else 0, member_id),
        )
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        if "UNIQUE" in str(exc):
            return err("A member with that email already exists", 409,
                       {"email": fields["email"]})
        return err("Member could not be updated", 400, {"reason": str(exc)})

    return ok(_decorate(_fetch(db, member_id)))


@bp.post("/<int:member_id>/renew")
def renew_member(member_id):
    """Extend the membership by one plan duration.

    An unexpired membership extends from its current expiry, so the member does
    not lose the days they already paid for. An already-expired one extends from
    today, because back-dating a renewal would hand them a membership that is
    still expired. "Extend by the plan duration" is ambiguous about which date
    to extend from; this is the reading that behaves correctly in both cases.
    """
    db = get_db()
    row = _fetch(db, member_id)
    if row is None:
        return err("Member not found", 404, {"id": member_id})

    today = date.today().strftime("%Y-%m-%d")
    renew_from = max(row["expiry_date"], today)
    new_expiry = calculate_expiry(renew_from, row["plan_duration_days"])

    db.execute(
        "UPDATE members SET expiry_date = ?, is_active = 1 WHERE id = ?",
        (new_expiry, member_id),
    )
    db.commit()

    member = _decorate(_fetch(db, member_id))
    member["renewed_from"] = renew_from
    member["previous_expiry"] = row["expiry_date"]
    return ok(member)


@bp.delete("/<int:member_id>")
def delete_member(member_id):
    db = get_db()
    if _fetch(db, member_id) is None:
        return err("Member not found", 404, {"id": member_id})
    # Bookings CASCADE — they have no meaning without their member.
    db.execute("DELETE FROM members WHERE id = ?", (member_id,))
    db.commit()
    return ok({"deleted": member_id})
