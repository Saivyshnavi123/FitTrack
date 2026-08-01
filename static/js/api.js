/*
 * api.js — the only place this application talks to the server.
 *
 * Every call goes through fetch(). Nothing here reloads the page, and no
 * <form> is ever submitted.
 *
 * The API always answers with the same envelope:
 *     { "success": true,  "data": {...} }
 *     { "success": false, "error": "Class is full", "details": {...} }
 *
 * request() unwraps that envelope so callers deal in plain data, and turns any
 * failure into an ApiError carrying the server's own message, status code and
 * details — which is what lets the UI show *why* something was refused rather
 * than a generic failure.
 */

class ApiError extends Error {
  constructor(message, status, details) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.details = details || {};
  }
}

const API = {
  async request(method, path, body) {
    const options = { method, headers: {} };
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(path, options);
    } catch (networkError) {
      throw new ApiError('Cannot reach the server. Is Flask running?', 0);
    }

    // Flask returns JSON for every route including 404 and 500, so anything
    // that fails to parse means something is genuinely wrong.
    let payload;
    try {
      payload = await response.json();
    } catch (parseError) {
      throw new ApiError(
        `Server returned a non-JSON response (HTTP ${response.status})`,
        response.status
      );
    }

    if (!response.ok || payload.success === false) {
      throw new ApiError(
        payload.error || `Request failed (HTTP ${response.status})`,
        response.status,
        payload.details
      );
    }
    return payload.data;
  },

  get(path)        { return this.request('GET', path); },
  post(path, body) { return this.request('POST', path, body); },
  put(path, body)  { return this.request('PUT', path, body); },
  del(path)        { return this.request('DELETE', path); },
};
