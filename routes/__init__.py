"""HTTP layer — one Flask blueprint per entity.

Route modules are responsible only for: reading the request, calling into
services/ or the database, and returning a JSON envelope with the right status
code. They contain no business logic, so that the rules stay unit-testable
without Flask (see services/booking_rules.py).

Flask returns JSON only and never renders a template.
"""
