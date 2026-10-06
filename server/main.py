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

from server import service, storage  # noqa: E402

WEB_DIST = BASE_DIR / "web" / "dist"

app = Flask(__name__)


def _repo_ready(rid: str):
    """(meta, None) if the repo exists and is ready, else (None, (resp, code))."""
    meta = storage.get_repo(rid)
    if not meta:
        return None, (jsonify(error="repo not found"), 404)
    if meta.get("status") != "ready":
        return None, (jsonify(error=f"repo is not ready (status: {meta.get('status')})"), 409)
    return meta, None


def _loaded(meta: dict):
    """(service entry, None) or (None, (resp, code)) when loading fails."""
    try:
        return service.load(meta), None
    except Exception as e:  # noqa: BLE001
        return None, (jsonify(error=f"failed to load repo data: {e}"), 500)


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
# Authors (T6): canonical list, .mailmap view, manual merge groups
# ---------------------------------------------------------------------------

@app.get("/api/repos/<rid>/authors")
def api_repo_authors(rid: str):
    meta, err = _repo_ready(rid)
    if err:
        return err
    entry, err = _loaded(meta)
    if err:
        return err
    return jsonify(authors=entry["authors"], count=len(entry["authors"]))


@app.get("/api/repos/<rid>/mailmap")
def api_repo_mailmap(rid: str):
    meta, err = _repo_ready(rid)
    if err:
        return err
    entry, err = _loaded(meta)
    if err:
        return err
    return jsonify(entries=entry["mailmap"], count=len(entry["mailmap"]))


@app.get("/api/repos/<rid>/merges")
def api_get_merges(rid: str):
    meta = storage.get_repo(rid)
    if not meta:
        return jsonify(error="repo not found"), 404
    return jsonify(groups=meta.get("merges") or [], version=meta.get("merges_version") or 0)


@app.post("/api/repos/<rid>/merges")
def api_set_merges(rid: str):
    """Replace the repo's merge groups. Members/canonical must be known base keys."""
    meta, err = _repo_ready(rid)
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    groups = payload.get("groups")
    if not isinstance(groups, list):
        return jsonify(error='Send {"groups": [{"canonical": "...", "members": ["..."]}]}'), 400
    entry, err = _loaded(meta)
    if err:
        return err
    allowed = set(entry["base_keys"])
    cleaned: list[dict] = []
    seen: dict[str, str] = {}
    for g in groups:
        if not isinstance(g, dict):
            return jsonify(error="each group must be an object"), 400
        canonical = str(g.get("canonical") or "").strip()
        members = [str(m).strip() for m in (g.get("members") or []) if str(m).strip()]
        if canonical not in allowed:
            return jsonify(error=f"canonical is not a known author key: {canonical!r}"), 400
        unknown = [m for m in members if m not in allowed]
        if unknown:
            return jsonify(error=f"unknown member keys: {unknown}"), 400
        for m in members:
            if m in seen and seen[m] != canonical:
                return jsonify(error=f"member {m!r} appears in multiple groups"), 400
            seen[m] = canonical
        cleaned.append({"canonical": canonical, "members": members})
    version = int(meta.get("merges_version") or 0) + 1
    storage.update_repo(rid, merges=cleaned, merges_version=version)
    service.invalidate(rid)
    return jsonify(groups=cleaned, version=version)


# ---------------------------------------------------------------------------
# Metrics + pickers (T7): POST /metrics, GET /commits, GET /tree
# ---------------------------------------------------------------------------

_COMMIT_MODES = ("all", "range", "list")


@app.post("/api/repos/<rid>/metrics")
def api_metrics(rid: str):
    """Metrics for a path + commit set + author filter (PLAN §7 contract)."""
    meta, err = _repo_ready(rid)
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    commits = payload.get("commits") or {"mode": "all"}
    if not isinstance(commits, dict) or (commits.get("mode") or "all") not in _COMMIT_MODES:
        return jsonify(error=f"commits.mode must be one of {list(_COMMIT_MODES)}"), 400
    author_keys = payload.get("authorKeys") or []
    if not isinstance(author_keys, list) or not all(isinstance(k, str) for k in author_keys):
        return jsonify(error="authorKeys must be a list of strings"), 400
    try:
        result = service.metric_query(
            meta,
            path=payload.get("path") or "",
            commits=commits,
            author_keys=author_keys,
        )
    except KeyError as e:
        return jsonify(error=e.args[0] if e.args else "unknown path"), 400
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(result)


@app.get("/api/repos/<rid>/commits")
def api_repo_commits(rid: str):
    """Newest-first commit list for the manual selection picker."""
    meta, err = _repo_ready(rid)
    if err:
        return err
    entry, err = _loaded(meta)
    if err:
        return err
    try:
        limit = int(request.args.get("limit") or 100)
    except ValueError:
        return jsonify(error="limit must be an integer"), 400
    limit = max(1, min(limit, 1000))
    q = (request.args.get("q") or "").strip().lower()
    engine = entry["engine"]
    rows = []
    for c in entry["data"].commits:  # newest first
        key = engine.author_meta[c.author_id][0]
        if q and q not in c.subject.lower() \
                and not c.hash.lower().startswith(q) and q not in key.lower():
            continue
        rows.append({"hash": c.hash, "ts": c.ts, "subject": c.subject, "author": key})
    return jsonify(commits=rows[:limit], total=len(rows), limit=limit)


@app.get("/api/repos/<rid>/tree")
def api_repo_tree(rid: str):
    """All queryable paths (files and dirs, dirs with trailing /) for the picker."""
    meta, err = _repo_ready(rid)
    if err:
        return err
    entry, err = _loaded(meta)
    if err:
        return err
    rows = [
        {"path": p, "type": kind}
        for kind, p in entry["engine"].objects
        if p != ""
    ]
    rows.sort(key=lambda r: r["path"])
    return jsonify(tree=rows, count=len(rows))


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
