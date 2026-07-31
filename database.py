"""SQLite connection handling.

Every connection sets PRAGMA foreign_keys = ON. SQLite defaults it OFF and it is
a *connection-level* setting, not a database-level one — so setting it once in
schema.sql is not enough. Without this the ON DELETE RESTRICT protecting plans
in use, and the CASCADE on bookings, would both be silently ignored.
"""

import sqlite3

from flask import current_app, g


def connect(path, timeout=5.0):
    """Open a connection with row access by name and foreign keys enforced.

    `timeout` is how long a connection waits for a write lock held by another
    connection before raising SQLITE_BUSY. It matters for booking creation,
    which takes an explicit BEGIN IMMEDIATE so that the capacity check and the
    insert cannot interleave with a competing booking.
    """
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """Connection for the current request, opened lazily and reused."""
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(exc=None):
    """Registered as the Flask teardown handler in app.create_app()."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(db_path, schema_path):
    """Create the schema. Drops and recreates every table."""
    conn = connect(db_path)
    with open(schema_path, encoding="utf-8") as fh:
        conn.executescript(fh.read())
    conn.commit()
    conn.close()


def row_to_dict(row):
    """sqlite3.Row is not JSON-serialisable; jsonify needs a plain dict."""
    return dict(row) if row is not None else None


def rows_to_list(rows):
    return [dict(r) for r in rows]
