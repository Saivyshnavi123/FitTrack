"""FitTrack — Flask application factory.

Flask returns JSON only and never renders a template. index.html is
delivered as a *static file*; no Jinja rendering happens anywhere in this app.
Every response, including framework errors like 404 and 405, goes out through
the envelope in responses.py.
"""

from flask import Flask

from config import Config
from database import close_db
from responses import err
from validators import ValidationError


def create_app(test_config=None):
    app = Flask(__name__, static_folder="static", static_url_path="")
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    app.teardown_appcontext(close_db)

    from routes.plans import bp as plans_bp
    from routes.members import bp as members_bp
    from routes.classes import bp as classes_bp, holidays_bp
    from routes.bookings import bp as bookings_bp
    app.register_blueprint(plans_bp)
    app.register_blueprint(members_bp)
    app.register_blueprint(classes_bp)
    app.register_blueprint(holidays_bp)
    app.register_blueprint(bookings_bp)

    # --- static delivery only: send_static_file, never render_template ---
    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    # --- every error is JSON, so the frontend never has to parse HTML ---
    @app.errorhandler(ValidationError)
    def _validation(exc):
        return err(exc.message, 400, exc.details or None)

    @app.errorhandler(400)
    def _bad_request(exc):
        return err("Malformed request", 400)

    @app.errorhandler(404)
    def _not_found(exc):
        return err("Not found", 404)

    @app.errorhandler(405)
    def _not_allowed(exc):
        return err("Method not allowed", 405)

    @app.errorhandler(500)
    def _server_error(exc):
        return err("Internal server error", 500)

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=5000)
