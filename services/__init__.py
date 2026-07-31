"""Business logic and external integrations.

booking_rules.py    the six booking rules, expiry, clash detection
holiday_service.py  Nager.Date fetch, "Public" type classification, cache, fallback

The rule functions and the parsing helpers are written as pure functions — no
database, no Flask, no network — so they can be unit tested in isolation. The
external API is called from here, never from the browser.
"""
