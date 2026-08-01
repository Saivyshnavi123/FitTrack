"""Create the database and load FIREFIT sample data.

    python seed.py

Class capacities are 8-12, matching FIREFIT's real published small-group sizes.
The two rooms are the Churchtown and Knocklyon studios.

No network call is made: the seed is entirely offline and identical every run.

THE SEED IS BUILT SO ALL SIX BOOKING RULES FIRE IN ONE OR TWO CLICKS.
See the demo cheat sheet in README.md for which member and class triggers each.
The arrangement below is deliberate:

  - Ruth Behan is is_active = 0                          -> rule 1
  - Spin 45 on 2026-08-12 is is_cancelled = 1            -> rule 2
  - Monthly members expire before the March 2027 classes -> rule 3
  - Reformer Flow on 2026-08-11 is seeded 7 of 8 full    -> rule 4
  - Grainne is already booked on HIIT 45 on 2026-08-04   -> rule 5
  - Spin 45 on 2026-08-04 overlaps that HIIT 45 class    -> rule 6

Also deliberate: no class on 2026-08-03 (August Holiday, a Public closure day),
and nothing on 2027-03-17 or 2027-03-26 so St Patrick's Day and Good Friday can
be created live in the demo. Classes 6 and 7 are back-to-back at 18:30, which
must NOT register as a clash (strict inequalities).

Prints counts only, never member or holiday names: this machine's console is
cp1252 and would mangle or fail on the non-ASCII in the sample data.
"""

from config import Config
from database import connect, init_db
from services.booking_rules import calculate_expiry

# name, price_eur, duration_days, description
PLANS = [
    ("Off-Peak Monthly", 49.00, 30,
     "Weekday classes before 16:00."),
    ("Unlimited Monthly", 89.00, 30,
     "All classes, both studios."),
    ("Class Pack 10", 120.00, 90,
     "Ten classes to use within three months."),
    ("Annual Unlimited", 799.00, 365,
     "Twelve months, all classes, both studios. Best value."),
    ("Taster Pack", 25.00, 30,
     "A cheap first month for new members."),
]

# first, last, email, phone, plan_index, start_date, is_active
MEMBERS = [
    ("Aoife", "Byrne", "aoife.byrne@example.com", "085 123 4401", 1, "2026-07-15", 1),
    ("Cian", "Murphy", "cian.murphy@example.com", "086 123 4402", 3, "2026-02-01", 1),
    ("Niamh", "O'Connor", "niamh.oconnor@example.com", "087 123 4403", 3, "2026-06-10", 1),
    ("Darragh", "Kelly", "darragh.kelly@example.com", "089 123 4404", 0, "2026-07-20", 1),
    ("Sinéad", "Walsh", "sinead.walsh@example.com", "085 123 4405", 2, "2026-06-01", 1),
    ("Eoin", "Doyle", "eoin.doyle@example.com", "086 123 4406", 1, "2026-05-01", 1),
    ("Ciara", "Nolan", "ciara.nolan@example.com", "087 123 4407", 0, "2026-06-25", 1),
    ("Fionn", "Gallagher", "fionn.gallagher@example.com", "089 123 4408", 3, "2025-09-01", 1),
    # --- demo members ---
    ("Ruth", "Behan", "ruth.behan@example.com", "085 123 4409", 1, "2026-07-25", 0),
    ("Oisín", "Farrell", "oisin.farrell@example.com", "086 123 4410", 4, "2026-07-25", 1),
    ("Gráinne", "Doherty", "grainne.doherty@example.com", "087 123 4411", 1, "2026-07-25", 1),
    ("Killian", "Brady", "killian.brady@example.com", "089 123 4412", 3, "2026-07-01", 1),
    ("Orla", "Sheridan", "orla.sheridan@example.com", "085 123 4413", 3, "2026-07-01", 1),
]

# name, instructor, date, start, end, capacity, room, cancelled
CLASSES = [
    # --- past, with attendance, so the attendance report has real data ---
    ("Strength Circuit", "Dave O'Brien", "2026-07-20", "18:00", "19:00", 10,
     "Knocklyon", 0),
    ("HIIT 45", "Mark Ryan", "2026-07-22", "07:00", "07:45", 12,
     "Churchtown", 0),

    # --- upcoming, bookable today ---
    ("HIIT 45", "Mark Ryan", "2026-08-04", "06:30", "07:15", 12,
     "Knocklyon", 0),
    ("Reformer Flow", "Laura Fitzgerald", "2026-08-04", "18:00", "19:00", 8,
     "Churchtown", 0),
    ("Strength Circuit", "Dave O'Brien", "2026-08-05", "07:00", "08:00", 10,
     "Knocklyon", 0),
    ("Spin 45", "Laura Fitzgerald", "2026-08-05", "09:30", "10:15", 12,
     "Churchtown", 0),
    ("Reformer Flow", "Laura Fitzgerald", "2026-08-06", "17:30", "18:30", 8,
     "Churchtown", 0),
    ("Hyrox Prep", "Mark Ryan", "2026-08-06", "18:30", "19:30", 10,
     "Knocklyon", 0),
    ("HIIT 45", "Dave O'Brien", "2026-08-07", "07:00", "07:45", 12,
     "Churchtown", 0),
    ("Strength Circuit", "Mark Ryan", "2026-08-08", "10:00", "11:00", 12,
     "Knocklyon", 0),

    # --- March 2027, the holiday demo window ---
    ("Strength Circuit", "Dave O'Brien", "2027-03-15", "18:00", "19:00", 10,
     "Knocklyon", 0),
    ("HIIT 45", "Mark Ryan", "2027-03-16", "07:00", "07:45", 12,
     "Churchtown", 0),
    ("Reformer Flow", "Laura Fitzgerald", "2027-03-18", "18:00", "19:00", 8,
     "Churchtown", 0),

    # --- booking-rule demo classes ---
    # 13: an ordinary past class, kept for member booking history
    ("Strength Circuit", "Dave O'Brien", "2026-07-27", "18:00", "19:00", 10,
     "Knocklyon", 0),
    # 14: overlaps class 2 (06:30-07:15) on the same day -> rule 6
    ("Spin 45", "Laura Fitzgerald", "2026-08-04", "07:00", "07:45", 12,
     "Churchtown", 0),
    # 15: seeded 7 of 8 -> one booking fills it, the next hits rule 4
    ("Reformer Flow", "Laura Fitzgerald", "2026-08-11", "18:00", "19:00", 8,
     "Churchtown", 0),
    # 16: cancelled -> rule 2
    ("Spin 45", "Laura Fitzgerald", "2026-08-12", "09:30", "10:15", 12,
     "Churchtown", 1),
    # 17: a second early class in Knocklyon
    ("Core Strength", "Mark Ryan", "2026-08-13", "07:00", "07:45", 12,
     "Knocklyon", 0),
]

# member_index, class_index, status
BOOKINGS = [
    # past classes, for the attendance report
    (0, 0, "attended"), (1, 0, "attended"), (3, 0, "no_show"),
    (2, 1, "attended"), (4, 1, "attended"), (7, 1, "no_show"),

    # ordinary upcoming bookings
    (0, 2, "booked"), (1, 2, "booked"), (3, 3, "booked"),
    (2, 5, "booked"), (1, 7, "booked"), (4, 8, "booked"), (7, 9, "booked"),

    # rules 5 and 6: Grainne already on HIIT 45, 2026-08-04 06:30-07:15
    (10, 2, "booked"),

    # ordinary bookings for Oisin
    (9, 4, "booked"), (9, 7, "booked"),

    # rule 4: Reformer Flow on 2026-08-11 seeded to 7 of 8
    (0, 15, "booked"), (1, 15, "booked"), (2, 15, "booked"), (3, 15, "booked"),
    (4, 15, "booked"), (7, 15, "booked"), (10, 15, "booked"),
]


def seed(db_path=None, schema_path=None):
    db_path = db_path or Config.DATABASE
    schema_path = schema_path or Config.SCHEMA

    init_db(db_path, schema_path)
    conn = connect(db_path)

    plan_ids = []
    for row in PLANS:
        cur = conn.execute(
            """INSERT INTO plans (name, price_eur, duration_days, description)
               VALUES (?, ?, ?, ?)""", row)
        plan_ids.append(cur.lastrowid)

    member_ids = []
    for first, last, email, phone, plan_idx, start, active in MEMBERS:
        duration = PLANS[plan_idx][2]
        cur = conn.execute(
            """INSERT INTO members (first_name, last_name, email, phone,
                                    plan_id, start_date, expiry_date, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (first, last, email, phone, plan_ids[plan_idx], start,
             calculate_expiry(start, duration), active))
        member_ids.append(cur.lastrowid)

    class_ids = []
    for row in CLASSES:
        cur = conn.execute(
            """INSERT INTO classes (name, instructor, class_date, start_time,
                                    end_time, capacity, room, is_cancelled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", row)
        class_ids.append(cur.lastrowid)

    for member_idx, class_idx, status in BOOKINGS:
        conn.execute(
            """INSERT INTO bookings (member_id, class_id, status)
               VALUES (?, ?, ?)""",
            (member_ids[member_idx], class_ids[class_idx], status))

    conn.commit()
    counts = {
        t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
        for t in ("plans", "members", "classes", "bookings")
    }
    conn.close()
    return counts


if __name__ == "__main__":
    result = seed()
    print(f"Seeded {Config.GYM_NAME} database at {Config.DATABASE}")
    for table, n in result.items():
        print(f"  {table:<10} {n}")
    print("\nAll six booking rules are demonstrable -- see README.md cheat sheet.")
