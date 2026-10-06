"""RAT — Repo Analysis Tool.

Flask app: JSON API under /api plus the built frontend served from web/dist.
Run from the project root:  .venv/bin/python -m server.main
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402

from server import storage  # noqa: E402

WEB_DIST = BASE_DIR / "web" / "dist"

app = Flask(__name__)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return jsonify(status="ok", service="rat", version="0.1.0")


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

@app.get("/api/repos")
def api_list_repos():
    return jsonify(storage.list_repos())


@app.post("/api/repos")
def api_create_repo():
    if "file" in request.files:
        try:
            meta = storage.register_zip(request.files["file"])
        except ValueError as e:
            return jsonify(error=str(e)), 400
        except Exception as e:  # noqa: BLE001
            return jsonify(error=f"Failed to ingest zip: {e}"), 500
        return jsonify(meta), 201

    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    if url:
        try:
            meta = storage.register_clone(url)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(meta), 201

    return jsonify(error="Provide a zip file (multipart field 'file') or JSON {\"url\": \"...\"}."), 400


@app.get("/api/repos/<rid>/status")
def api_repo_status(rid: str):
    meta = storage.get_repo(rid)
    if not meta:
        return jsonify(error="repo not found"), 404
    return jsonify(meta)


@app.delete("/api/repos/<rid>")
def api_delete_repo(rid: str):
    if not storage.delete_repo(rid):
        return jsonify(error="repo not found"), 404
    return jsonify(ok=True)


# ---------------------------------------------------------------------------
# Static frontend (production build in web/dist; dev uses the Vite server)
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    if (WEB_DIST / "index.html").exists():
        return send_from_directory(WEB_DIST, "index.html")
    return jsonify(
        message="Frontend not built yet. Use the Vite dev server (cd web && npm run dev) "
                "or build it (cd web && npm run build)."
    )


@app.get("/<path:filepath>")
def static_files(filepath: str):
    if filepath.startswith("api/"):
        return jsonify(error="not found"), 404
    target = WEB_DIST / filepath
    if target.is_file():
        return send_from_directory(WEB_DIST, filepath)
    if (WEB_DIST / "index.html").exists():
        return send_from_directory(WEB_DIST, "index.html")  # SPA fallback
    return jsonify(error="not found"), 404


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False, threaded=True)
