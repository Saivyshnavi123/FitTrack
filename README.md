# FitTrack

A membership and class-booking system for **FIREFIT**, a small independent gym in south
Dublin running coached small-group classes across two studios, Churchtown and Knocklyon.

Flask REST API · SQLite · vanilla JavaScript. No build step, no framework on the frontend,
no API keys, no accounts. Clone it and run it.

---

## Quick start

```bash
pip install -r requirements.txt
python seed.py          # creates fittrack.db with sample data
python app.py           # http://127.0.0.1:5000
```

Requires Python 3.11 or later. `seed.py` makes no network call and produces identical data
every run, so the demo state is always the same.

```bash
python -m pytest                    # 20 tests
python -m coverage run -m pytest    # with coverage
python -m coverage report           # 94% statement coverage
```

---

## What it does

Four resources, full CRUD on each, plus the business logic that governs them.

| | |
|---|---|
| **Plans** | Membership tiers with a price and a duration in days |
| **Members** | People on a plan. Expiry is calculated server-side and stored |
| **Classes** | The timetable, with an instructor, a studio and a capacity |
| **Bookings** | A member's place in a class, subject to six rules |

### Features

- **Calculated membership expiry.** Set a plan and a start date; the server computes
  `start_date + duration_days` and stores it. Editing a plan later does not move the expiry
  of members already on it.
- **Renewal that behaves sensibly.** A live membership extends from its current expiry so
  paid days are not lost. An expired one extends from today, because back-dating would
  produce a membership that is still expired.
- **Six booking rules**, checked in order, first failure returned.
- **Instructor clash detection.** The same interval-overlap function backs both the
  member-facing booking rule and the staff-facing scheduling check, so they cannot drift.
- **Public holiday closure check.** Classes cannot be scheduled on days the gym closes.
- **Cancellation without deletion.** Cancelling frees the place immediately but keeps the
  row, so the gym retains its history.
- **Capacity floor.** A class's capacity cannot be edited below the places already taken.
- **Filtering.** Members by plan; classes by date, instructor, or upcoming-only; bookings by
  member or status.

---

## How it works

### JSON only, all the way down

Flask never renders a template. `index.html` is delivered by `send_static_file`, and every
response, including 404, 405 and 500, goes out through the same envelope:

```json
{ "success": true,  "data": { ... } }
{ "success": false, "error": "Class is full", "details": { "capacity": 8, "booked": 8 } }
```

The frontend contains no `<form>` element, so nothing can submit or navigate. Every
interaction is a `fetch()` call whose result is written into the DOM. Open DevTools →
Network and click around: the page loads once and never again.

### The six booking rules

Evaluated in this order; the first failure is what you get back, so the member is told the
problem they have to fix first.

| # | Rule | Response |
|---|---|---|
| 1 | Member exists and is active | `409` Member is not active |
| 2 | Class exists and is not cancelled | `409` Class has been cancelled |
| 3 | Expiry is on or after the class date | `409` Membership expires before this class |
| 4 | Places taken are fewer than capacity | `409` Class is full |
| 5 | Member holds no live booking for this class | `409` Already booked |
| 6 | No time overlap with another booking that day | `409` Clashes with an existing booking |

Two boundaries worth knowing: a membership expiring **on** the class date is valid (rule 3
uses `>=`), and clash detection uses **strict** inequalities, so a class ending at 18:30 and
one starting at 18:30 do not clash.

### The closure rule

Public holidays come from [Nager.Date](https://date.nager.at/Api). The rule is **not** "is
this a holiday" — it is:

```python
is_closure = "Public" in holiday["types"]
```

In Ireland, Good Friday returns `["Bank", "School"]` while every other entry returns
`["Public"]`. Banks and schools close; a gym trades. Blocking on every holiday would close
FIREFIT on a day it opens. A Bank/School-only day is therefore allowed, with an advisory
naming the holiday shown in the UI.

The call is made from Flask, never from the browser — a rule living in JavaScript could be
bypassed by editing the page, and a browser-side response could not be cached. Each year is
fetched once, written to `holiday_cache` with `is_closure` computed on insert, and every
later check is a local lookup.

Four paths are handled: live fetch, cache hit (zero network calls), API down with the year
cached (cache answers), and API down with nothing cached (class allowed, with a warning).
The last is a deliberate trade-off — an unusable scheduling screen is worse than a class on
a closed day, which a human will spot.

### Cancel and rebook

`DELETE /api/bookings/<id>` sets `status = 'cancelled'` rather than removing the row.
`UNIQUE(member_id, class_id)` makes a duplicate impossible, so re-booking **reactivates** the
existing row and the response carries `reactivated: true`. One auditable row per
member/class pair, and the freed place is available at once because every occupancy count
filters on `booked` and `attended`.

### Concurrency

Two members racing for the last place cannot both get it. Booking creation opens
`BEGIN IMMEDIATE`, taking the write lock up front, and re-reads the capacity count *inside*
the transaction before inserting. A threaded test asserts that exactly one of two
simultaneous attempts succeeds.

### Foreign keys

SQLite disables foreign key enforcement by default, and the setting is **per connection**,
not per database. `database.py` issues `PRAGMA foreign_keys = ON` on every connection —
setting it once in `schema.sql` would leave every `RESTRICT` and `CASCADE` silently inert.

---

## API

23 endpoints across 5 blueprints. All return the JSON envelope above.

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/api/plans` · `/api/plans/<id>` | Includes a live member count |
| `POST` `PUT` `DELETE` | `/api/plans` · `/api/plans/<id>` | Delete refused with `409` if members are on it |
| `GET` | `/api/members?plan_id=` · `/api/members/<id>` | Detail includes plan and booking history |
| `POST` `PUT` `DELETE` | `/api/members` · `/api/members/<id>` | Expiry calculated on create |
| `POST` | `/api/members/<id>/renew` | Extends by one plan duration |
| `GET` | `/api/classes?date=&instructor=&upcoming=` · `/api/classes/<id>` | Detail includes bookings and spaces left |
| `POST` `PUT` `DELETE` | `/api/classes` · `/api/classes/<id>` | Closure check and clash check on create |
| `PATCH` | `/api/classes/<id>/cancel` | Keeps the class and its bookings |
| `GET` | `/api/bookings?member_id=&class_id=&status=` · `/api/bookings/<id>` | |
| `POST` `DELETE` | `/api/bookings` · `/api/bookings/<id>` | Six rules on create; delete cancels |
| `PATCH` | `/api/bookings/<id>/status` | `booked` · `attended` · `no_show` · `cancelled` |
| `GET` | `/api/holidays/<year>` | Cached list with `is_closure` |

Status codes: `200`, `201`, `400` validation, `404` not found, `409` rule violation or
conflict, `422` unknown foreign key, `503` upstream unavailable.

---

## What's in here

```
app.py                  application factory, blueprint registration, JSON error handlers
config.py               configuration; no credentials of any kind
database.py             connections, row factory, PRAGMA foreign_keys = ON
responses.py            the JSON envelope: ok(), created(), err()
validators.py           input validation for every entity field
seed.py                 creates the database and loads the sample data
schema.sql              5 tables, 4 indexes

routes/
  plans.py              membership plans CRUD
  members.py            members CRUD, calculated expiry, renewal
  classes.py            classes CRUD, closure check, instructor clash
  bookings.py           bookings CRUD, the six rules, transactional

services/
  booking_rules.py      pure logic: expiry, interval overlap, the six rules
  holiday_service.py    Nager.Date integration, closure rule, cache, fallback

static/
  index.html            four tabbed sections, no <form> anywhere
  js/api.js             the only place the app talks to the server
  js/app.js             all four views

tests/                  20 tests, temporary database per test
```

### Database

Five tables. `bookings` is the junction table resolving the many-to-many between members and
classes.

- `plans` — `UNIQUE(name)`, `CHECK` on price and duration
- `members` — `UNIQUE(email)`, `plan_id` → `plans` **`ON DELETE RESTRICT`**
- `classes` — `CHECK` on capacity
- `bookings` — `UNIQUE(member_id, class_id)`, both FKs **`ON DELETE CASCADE`**, `CHECK` on status
- `holiday_cache` — keyed by date, standalone, written by the holiday service

The delete rules differ on purpose. A plan people are paying for must not vanish; a booking
has no meaning once its member or class is gone.

### Sample data

`seed.py` loads 5 plans, 13 members, 18 classes and 23 bookings, arranged so every booking
rule can be demonstrated in a click or two — an inactive member, a cancelled class, a
membership expiring before a future class, a class seeded 7 of 8, an existing booking, and
an overlapping pair.

---

## Testing

20 tests, 94% statement coverage over 723 statements. Each test gets its own temporary
SQLite database via pytest's `tmp_path`, so nothing shares state. Nager.Date is mocked with
the **real captured payload**, not invented data.

| File | Tests | Covers |
|---|---:|---|
| `test_plans.py` | 2 | CRUD, duplicate name, delete blocked by RESTRICT |
| `test_members.py` | 2 | CRUD, calculated expiry, renewal, cascade |
| `test_classes.py` | 2 | CRUD, instructor clash, capacity floor, cancellation |
| `test_bookings.py` | 5 | All six rules, rule ordering, cancel/rebook, concurrency |
| `test_rules.py` | 3 | Interval overlap, expiry boundaries, closure classification |
| `test_external.py` | 3 | All four holiday paths |
| `test_integration.py` | 3 | Full stack, asserted against SQLite directly |

The integration tests assert against the database as well as through the API — a `200`
proves nothing on its own about what was actually stored.

---

## Built with

| | | |
|---|---|---|
| [Flask](https://flask.palletsprojects.com/) | 3.0.0 | BSD-3-Clause |
| [SQLite](https://sqlite.org/) | stdlib | Public domain |
| [Requests](https://requests.readthedocs.io/) | 2.32.5 | Apache 2.0 |
| [pytest](https://docs.pytest.org/) | 9.0.3 | MIT |
| [coverage.py](https://coverage.readthedocs.io/) | 7.15.1 | Apache 2.0 |
| [Nager.Date](https://date.nager.at/Api) | v3 API | MIT |


