"""Configuration. No credentials required anywhere.

The external service (Nager.Date) needs no API key, account or token, so the
application runs from a clean clone with nothing to configure.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    # --- gym identity ---
    GYM_NAME = "FIREFIT"
    GYM_URL = "https://firefit.ie"
    COUNTRY_CODE = "IE"          # drives the Nager.Date holiday lookup

    # --- storage ---
    DATABASE = os.path.join(BASE_DIR, "fittrack.db")
    SCHEMA = os.path.join(BASE_DIR, "schema.sql")

    # --- external service (keyless) ---
    NAGER_BASE = "https://date.nager.at/api/v3"
    HTTP_TIMEOUT = 10
    USER_AGENT = "FitTrack-Student-Project/1.0"

    TESTING = False
