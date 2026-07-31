"""Request payload validation.

Raises ValidationError, which app.create_app() maps to a 400 JSON envelope, so
route handlers stay free of validation branching.
"""

import re
from datetime import datetime

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ValidationError(Exception):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


def as_payload(data):
    """Reject anything that is not a JSON object before we index into it."""
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")
    return data


def _missing(payload, field):
    return field not in payload or payload[field] is None or payload[field] == ""


def text(payload, field, required=True, max_len=200, default=None):
    if _missing(payload, field):
        if required:
            raise ValidationError(f"'{field}' is required", {"field": field})
        return default
    value = str(payload[field]).strip()
    if required and not value:
        raise ValidationError(f"'{field}' cannot be blank", {"field": field})
    if len(value) > max_len:
        raise ValidationError(
            f"'{field}' must be {max_len} characters or fewer", {"field": field}
        )
    return value


def number(payload, field, required=True, minimum=None, default=None):
    if _missing(payload, field):
        if required:
            raise ValidationError(f"'{field}' is required", {"field": field})
        return default
    try:
        value = float(payload[field])
    except (TypeError, ValueError):
        raise ValidationError(f"'{field}' must be a number", {"field": field})
    if minimum is not None and value < minimum:
        raise ValidationError(
            f"'{field}' must be {minimum} or greater",
            {"field": field, "received": payload[field]},
        )
    return value


def integer(payload, field, required=True, minimum=None, nullable=False, default=None):
    """nullable=True distinguishes 'field absent' from 'field explicitly null'.

    Distinguishes a field the caller omitted from one they deliberately sent
    as null.
    """
    if field in payload and payload[field] is None and nullable:
        return None
    if _missing(payload, field):
        if required:
            raise ValidationError(f"'{field}' is required", {"field": field})
        return default
    try:
        value = int(payload[field])
        if float(payload[field]) != value:
            raise ValueError
    except (TypeError, ValueError):
        raise ValidationError(f"'{field}' must be a whole number", {"field": field})
    if minimum is not None and value < minimum:
        raise ValidationError(
            f"'{field}' must be {minimum} or greater",
            {"field": field, "received": payload[field]},
        )
    return value


def date_string(payload, field, required=True, default=None):
    """Dates are stored as YYYY-MM-DD text so SQLite string comparison sorts and
       ranges correctly — which is what makes the expiry and clash queries work."""
    if _missing(payload, field):
        if required:
            raise ValidationError(f"'{field}' is required", {"field": field})
        return default
    value = str(payload[field]).strip()
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValidationError(
            f"'{field}' must be a date in YYYY-MM-DD format",
            {"field": field, "received": payload[field]},
        )
    return value


def boolean(payload, field, default=True):
    if _missing(payload, field) and payload.get(field) is not False:
        return default
    value = payload[field]
    if isinstance(value, bool):
        return value
    if str(value).lower() in ("1", "true", "yes"):
        return True
    if str(value).lower() in ("0", "false", "no"):
        return False
    raise ValidationError(f"'{field}' must be true or false", {"field": field})


def email_address(payload, field="email"):
    value = text(payload, field, max_len=120)
    if not EMAIL_RE.match(value):
        raise ValidationError(
            "'email' is not a valid email address",
            {"field": field, "received": value},
        )
    return value.lower()


def validate_member(data):
    payload = as_payload(data)
    return {
        "first_name": text(payload, "first_name", max_len=60),
        "last_name": text(payload, "last_name", max_len=60),
        "email": email_address(payload),
        "phone": text(payload, "phone", required=False, max_len=30),
        "plan_id": integer(payload, "plan_id", minimum=1),
        "start_date": date_string(payload, "start_date"),
        "is_active": boolean(payload, "is_active", default=True),
    }


def time_string(payload, field, required=True, default=None):
    """Times are stored as zero-padded "HH:MM" text, which compares
       lexicographically in the same order it compares chronologically — that is
       what lets intervals_overlap() and the SQL both work on plain strings."""
    if _missing(payload, field):
        if required:
            raise ValidationError(f"'{field}' is required", {"field": field})
        return default
    value = str(payload[field]).strip()
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError:
        raise ValidationError(
            f"'{field}' must be a time in HH:MM format",
            {"field": field, "received": payload[field]},
        )
    return parsed.strftime("%H:%M")


def validate_class(data):
    payload = as_payload(data)
    return {
        "name": text(payload, "name", max_len=100),
        "instructor": text(payload, "instructor", max_len=80),
        "class_date": date_string(payload, "class_date"),
        "start_time": time_string(payload, "start_time"),
        "end_time": time_string(payload, "end_time"),
        "capacity": integer(payload, "capacity", minimum=1),
        "room": text(payload, "room", required=False, max_len=60),
    }


def validate_plan(data):
    """Full validation for POST and PUT. Mirrors the CHECK constraints in schema.sql
       so the caller gets a readable message instead of an IntegrityError."""
    payload = as_payload(data)
    return {
        "name": text(payload, "name", max_len=100),
        "price_eur": number(payload, "price_eur", minimum=0),
        "duration_days": integer(payload, "duration_days", minimum=1),
        "description": text(payload, "description", required=False, max_len=500),
    }
