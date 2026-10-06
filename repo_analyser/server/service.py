"""Repo service layer: cached RepoData/MetricsEngine per repository + query memo.

The engine is built once per (repo, merges_version); changing merge groups
bumps the version and invalidates it. Metric queries are memoized keyed by
(repo, merges_version, path, commit filter, author keys) so repeated UI
polling is instant.
"""
from __future__ import annotations

import json
import threading

from . import storage
from .engine.authors import AuthorResolver, load_mailmap
from .engine.metrics import MetricsEngine
from .engine.parse import parse_repo

_lock = threading.RLock()
_cache: dict[str, dict] = {}
_query_cache: dict[tuple, dict] = {}
_QUERY_CACHE_MAX = 512


def load(meta: dict) -> dict:
    """Cached {data, engine, mailmap, resolver, base_keys, authors} for a ready repo."""
    rid = meta["id"]
    version = int(meta.get("merges_version") or 0)
    with _lock:
        entry = _cache.get(rid)
        if entry is not None and entry["version"] == version:
            return entry

    git_dir = storage.git_dir_for(meta)
    if git_dir is None:
        raise ValueError("repo has no git directory yet")
    data = parse_repo(git_dir)
    mailmap = load_mailmap(git_dir)
    resolver = AuthorResolver(mailmap, meta.get("merges") or [])
    engine = MetricsEngine(data, resolver)
    base_keys = sorted({resolver.base_key(n, e) for (n, e) in data.authors})
    entry = {
        "version": version,
        "data": data,
        "engine": engine,
        "mailmap": mailmap,
        "resolver": resolver,
        "base_keys": base_keys,
        "authors": canonical_authors(data, resolver),
    }
    with _lock:
        _cache[rid] = entry
    return entry


def canonical_authors(data, resolver) -> list[dict]:
    """Canonical author rows: key/name/email, commit counts, raw identities."""
    rows: dict[str, dict] = {}
    for (n, e) in data.authors:
        key, kn, ke = resolver(n, e)
        row = rows.setdefault(
            key, {"key": key, "name": kn, "email": ke, "commits": 0, "raw": []}
        )
        row["raw"].append(f"{n} <{e}>")
    for c in data.commits:
        n, e = data.authors[c.author_id]
        key, _, _ = resolver(n, e)
        rows[key]["commits"] += 1
    return sorted(rows.values(), key=lambda r: (-r["commits"], r["key"]))


def metric_query(meta: dict, path: str = "", commits: dict | None = None,
                 author_keys: list | None = None) -> dict:
    """Memoized engine query; cache key = (repo, merge-version, filters)."""
    rid = meta["id"]
    version = int(meta.get("merges_version") or 0)
    norm = _norm_filter(commits or {"mode": "all"})
    key = (
        rid, version, path or "",
        json.dumps(norm, sort_keys=True),
        tuple(sorted(author_keys or [])),
    )
    with _lock:
        hit = _query_cache.get(key)
    if hit is not None:
        return hit
    entry = load(meta)
    result = entry["engine"].query(path=path, commits=norm, author_keys=author_keys)
    with _lock:
        if len(_query_cache) >= _QUERY_CACHE_MAX:
            _query_cache.clear()
        _query_cache[key] = result
    return result


def _norm_filter(commits: dict) -> dict:
    """Normalize a commit selector so equivalent requests hit the same cache key."""
    mode = commits.get("mode") or "all"
    norm: dict = {"mode": mode}
    if mode == "list":
        raw = commits.get("hashes")
        if not isinstance(raw, list):
            raw = []
        norm["hashes"] = sorted({str(h).strip() for h in raw if str(h).strip()})
    elif mode == "range":
        if commits.get("start") is not None:
            norm["start"] = int(commits["start"])
        if commits.get("end") is not None:
            norm["end"] = int(commits["end"])
    return norm


def invalidate(rid: str) -> None:
    """Drop cached engine + queries for a repo (after merge-group changes)."""
    with _lock:
        _cache.pop(rid, None)
        for k in [k for k in _query_cache if k[0] == rid]:
            del _query_cache[k]
