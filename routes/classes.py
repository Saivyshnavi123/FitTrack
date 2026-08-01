"""Class timetable endpoints.

    GET    /api/classes            ?date= &instructor= &upcoming=true
    GET    /api/classes/<id>       booking count, spaces left, bookings
    POST   /api/classes            closure check + instructor clash
    PUT    /api/classes/<id>       capacity cannot drop below bookings taken
    PATCH  /api/classes/<id>/cancel
    DELETE /api/classes/<id>
    GET    /api/holidays/<year>    cached list with is_closure
"""

from datetime import date

from flask import Blueprint, current_app, request

from database import get_db, rows_to_list
from responses import created, err, ok
from services.booking_rules import intervals_overlap
from services.holiday_service import cached_year, check_closure, ensure_year
from validators import validate_class

bp = Blueprint("classes", __name__, url_prefix="/api/classes")
holidays_bp = Blueprint("holidays", __name__, url_prefix="/api/holidays")

# A cancelled booking frees its place, so it does not count.
ACTIVE_BOOKING_STATUSES = ("booked", "attended")


def _booked_count(db, class_id):
    row = db.execute(
        """SELECT COUNT(*) AS n FROM bookings
            WHERE class_id = ? AND status IN (?, ?)""",
        (class_id, *ACTIVE_BOOKING_STATUSES),
    ).fetchone()
    return row["n"]


def _fetch(db, class_id):
    return db.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()


def _decorate(db, row):
    item = dict(row)
    item["booked_count"] = _booked_count(db, row["id"])
    item["spaces_left"] = max(0, row["capacity"] - item["booked_count"])
    item["is_full"] = item["spaces_left"] == 0
    item["is_past"] = row["class_date"] < date.today().strftime("%Y-%m-%d")
    return item


def _instructor_conflict(db, instructor, class_date, start, end, exclude_id=None):
    """The instructor must not already be teaching an overlapping class that
       day. Uses the SAME intervals_overlap() as booking rule 6."""
    sql = ("SELECT * FROM classes WHERE instructor = ? AND class_date = ? "
           "AND is_cancelled = 0")
    params = [instructor, class_date]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)

    for other in db.execute(sql, params).fetchall():
        if intervals_overlap(start, end, other["start_time"], other["end_time"]):
            return other
    return None


# --- read -----------------------------------------------------------------

@bp.get("")
def list_classes():
    db = get_db()
    sql = "SELECT * FROM classes"
    where, params = [], []

    if request.args.get("date"):
        where.append("class_date = ?")
        params.append(request.args["date"])
    if request.args.get("instructor"):
        where.append("instructor LIKE ?")
        params.append(f"%{request.args['instructor']}%")
    if (request.args.get("upcoming") or "").lower() == "true":
        where.append("class_date >= ?")
        params.append(date.today().strftime("%Y-%m-%d"))
    if (request.args.get("include_cancelled") or "").lower() != "true":
        where.append("is_cancelled = 0")

    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY class_date, start_time, id"

    return ok([_decorate(db, r) for r in db.execute(sql, params).fetchall()])


@bp.get("/<int:class_id>")
def get_class(class_id):
    db = get_db()
    row = _fetch(db, class_id)
    if row is None:
        return err("Class not found", 404, {"id": class_id})

    item = _decorate(db, row)
    item["bookings"] = rows_to_list(db.execute(
        """SELECT b.id, b.status, m.id AS member_id,
                  m.first_name, m.last_name, m.email
             FROM bookings b
             JOIN members m ON m.id = b.member_id
            WHERE b.class_id = ?
            ORDER BY m.last_name""",
        (class_id,),
    ).fetchall())
    return ok(item)


# --- write ----------------------------------------------------------------

@bp.post("")
def create_class():
    fields = validate_class(request.get_json(silent=True))
    db = get_db()

    if fields["end_time"] <= fields["start_time"]:
        return err("End time must be after start time", 422,
                   {"start_time": fields["start_time"],
                    "end_time": fields["end_time"]})

    # --- the closure rule: type-aware, not "is it a holiday" ---
    closure = check_closure(db, fields["class_date"], current_app.config)
    if closure["is_closure"]:
        return err(
            f"The gym is closed on {fields['class_date']} "
            f"({closure['holiday_name']})", 409,
            {"date": fields["class_date"],
             "holiday": closure["holiday_name"],
             "local_name": closure["local_name"],
             "types": closure["types"]},
        )

    clash = _instructor_conflict(db, fields["instructor"], fields["class_date"],
                                 fields["start_time"], fields["end_time"])
    if clash:
        return err(
            f"{fields['instructor']} is already teaching at that time", 409,
            {"conflicting_class": clash["name"], "class_id": clash["id"],
             "start_time": clash["start_time"], "end_time": clash["end_time"]},
        )

    cur = db.execute(
        """INSERT INTO classes (name, instructor, class_date, start_time,
                                end_time, capacity, room, is_cancelled)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
        (fields["name"], fields["instructor"], fields["class_date"],
         fields["start_time"], fields["end_time"], fields["capacity"],
         fields["room"]),
    )
    db.commit()

    item = _decorate(db, _fetch(db, cur.lastrowid))
    # Carried up to the UI so the Good Friday case is visible, not just present.
    item["advisory"] = closure["advisory"]
    item["warning"] = closure["warning"]
    item["holiday_check"] = closure
    return created(item)


@bp.put("/<int:class_id>")
def update_class(class_id):
    db = get_db()
    existing = _fetch(db, class_id)
    if existing is None:
        return err("Class not found", 404, {"id": class_id})

    fields = validate_class(request.get_json(silent=True))

    if fields["end_time"] <= fields["start_time"]:
        return err("End time must be after start time", 422,
                   {"start_time": fields["start_time"],
                    "end_time": fields["end_time"]})

    # Capacity cannot drop below the places already taken.
    taken = _booked_count(db, class_id)
    if fields["capacity"] < taken:
        return err(
            "Capacity cannot be lower than the number of bookings already taken",
            409, {"capacity": fields["capacity"], "booked": taken},
        )

    closure = check_closure(db, fields["class_date"], current_app.config)
    if closure["is_closure"]:
        return err(
            f"The gym is closed on {fields['class_date']} "
            f"({closure['holiday_name']})", 409,
            {"date": fields["class_date"], "holiday": closure["holiday_name"],
             "types": closure["types"]},
        )

    clash = _instructor_conflict(db, fields["instructor"], fields["class_date"],
                                 fields["start_time"], fields["end_time"],
                                 exclude_id=class_id)
    if clash:
        return err(
            f"{fields['instructor']} is already teaching at that time", 409,
            {"conflicting_class": clash["name"], "class_id": clash["id"]},
        )

    db.execute(
        """UPDATE classes
              SET name = ?, instructor = ?, class_date = ?, start_time = ?,
                  end_time = ?, capacity = ?, room = ?
            WHERE id = ?""",
        (fields["name"], fields["instructor"], fields["class_date"],
         fields["start_time"], fields["end_time"], fields["capacity"],
         fields["room"], class_id),
    )
    db.commit()

    item = _decorate(db, _fetch(db, class_id))
    item["advisory"] = closure["advisory"]
    item["warning"] = closure["warning"]
    return ok(item)


@bp.patch("/<int:class_id>/cancel")
def cancel_class(class_id):
    db = get_db()
    if _fetch(db, class_id) is None:
        return err("Class not found", 404, {"id": class_id})
    db.execute("UPDATE classes SET is_cancelled = 1 WHERE id = ?", (class_id,))
    db.commit()
    return ok(_decorate(db, _fetch(db, class_id)))


@bp.delete("/<int:class_id>")
def delete_class(class_id):
    db = get_db()
    if _fetch(db, class_id) is None:
        return err("Class not found", 404, {"id": class_id})
    # Bookings CASCADE — a booking means nothing without its class.
    db.execute("DELETE FROM classes WHERE id = ?", (class_id,))
    db.commit()
    return ok({"deleted": class_id})


# --- holidays -------------------------------------------------------------

@holidays_bp.get("/<int:year>")
def get_holidays(year):
    db = get_db()
    source = ensure_year(db, year, current_app.config)
    holidays = cached_year(db, year)

    if source == "unavailable" and not holidays:
        return err(
            f"Public holiday data for {year} is unavailable and nothing is cached",
            503, {"year": year, "source": source},
        )

    return ok({
        "year": year,
        "country_code": current_app.config["COUNTRY_CODE"],
        "source": source,
        "closures": sum(1 for h in holidays if h["is_closure"]),
        "holidays": holidays,
    })
