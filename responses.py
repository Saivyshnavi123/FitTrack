"""The single JSON envelope used by every endpoint.

    success:  { "success": true,  "data": {...} }
    failure:  { "success": false, "error": "Class is full", "details": {...} }

Flask returns JSON only and never renders a template, so even
framework-level errors such as 404 and 405 are funnelled through err() by the
error handlers registered in app.create_app().
"""

from flask import jsonify


def ok(data=None, status=200):
    return jsonify({"success": True, "data": data if data is not None else {}}), status


def created(data=None):
    return ok(data, 201)


def err(message, status=400, details=None):
    body = {"success": False, "error": message}
    if details:
        body["details"] = details
    return jsonify(body), status
