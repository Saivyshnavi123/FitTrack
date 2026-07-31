"""Membership plan endpoints.

    GET    /api/plans
    GET    /api/plans/<id>
    POST   /api/plans
    PUT    /api/plans/<id>
    DELETE /api/plans/<id>      409 if members are on the plan
"""

import sqlite3

from flask import Blueprint, request

from database import get_db, row_to_dict, rows_to_list
from responses import created, err, ok
from validators import validate_plan

bp = Blueprint("plans", __name__, url_prefix="/api/plans")


def _fetch(db, plan_id):
    return db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()


def _member_count(db, plan_id):
    row = db.execute(
        "SELECT COUNT(*) AS n FROM members WHERE plan_id = ?", (plan_id,)
    ).fetchone()
    return row["n"]


@bp.get("")
def list_plans():
    db = get_db()
    rows = db.execute(
        """SELECT p.*, COUNT(m.id) AS member_count
             FROM plans p
             LEFT JOIN members m ON m.plan_id = p.id
            GROUP BY p.id
            ORDER BY p.price_eur"""
    ).fetchall()
    return ok(rows_to_list(rows))


@bp.get("/<int:plan_id>")
def get_plan(plan_id):
    row = _fetch(get_db(), plan_id)
    if row is None:
        return err("Plan not found", 404, {"id": plan_id})
    return ok(row_to_dict(row))


@bp.post("")
def create_plan():
    fields = validate_plan(request.get_json(silent=True))
    db = get_db()
    try:
        cur = db.execute(
            """INSERT INTO plans (name, price_eur, duration_days, description)
               VALUES (?, ?, ?, ?)""",
            (fields["name"], fields["price_eur"], fields["duration_days"],
             fields["description"]),
        )
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        # UNIQUE(name) is the only uniqueness constraint on this table.
        if "UNIQUE" in str(exc):
            return err("A plan with that name already exists", 409,
                       {"name": fields["name"]})
        return err("Plan could not be created", 400, {"reason": str(exc)})
    return created(row_to_dict(_fetch(db, cur.lastrowid)))


@bp.put("/<int:plan_id>")
def update_plan(plan_id):
    db = get_db()
    if _fetch(db, plan_id) is None:
        return err("Plan not found", 404, {"id": plan_id})

    fields = validate_plan(request.get_json(silent=True))
    try:
        db.execute(
            """UPDATE plans
                  SET name = ?, price_eur = ?, duration_days = ?, description = ?
                WHERE id = ?""",
            (fields["name"], fields["price_eur"], fields["duration_days"],
             fields["description"], plan_id),
        )
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        if "UNIQUE" in str(exc):
            return err("A plan with that name already exists", 409,
                       {"name": fields["name"]})
        return err("Plan could not be updated", 400, {"reason": str(exc)})

    # Note: existing members keep the expiry_date calculated under the OLD
    # duration. That is deliberate.
    return ok(row_to_dict(_fetch(db, plan_id)))


@bp.delete("/<int:plan_id>")
def delete_plan(plan_id):
    db = get_db()
    if _fetch(db, plan_id) is None:
        return err("Plan not found", 404, {"id": plan_id})

    # Checked in the application AND enforced by ON DELETE RESTRICT in the
    # schema. Defence in depth: the application check gives a useful message,
    # the constraint guarantees it even if this check were bypassed.
    in_use = _member_count(db, plan_id)
    if in_use:
        return err(
            "Cannot delete a plan that members are on", 409,
            {"plan_id": plan_id, "members": in_use},
        )

    try:
        db.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return err("Cannot delete a plan that members are on", 409,
                   {"plan_id": plan_id})
    return ok({"deleted": plan_id})
