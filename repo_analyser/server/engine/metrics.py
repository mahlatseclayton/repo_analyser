"""Metrics engine: rollups and commit-set/author aggregations over RepoData.

Implements the metric contract of PLAN.md §4:

  - every changed path rolls its deltas up onto all ancestor directories
    plus the root ("") — the root row is the repository metric
  - n (modifications) = number of commits in H with lambda > 0 on the object
  - eta (frequency) = n / |H|, rho (churn rate) = lambda / |H| (0 when |H| = 0)
  - author dimensions per object: n_{H,o,a}, lambda_{H,o,a},
    omega = lambda_{H,o,a} / lambda_o (0 when lambda_o = 0)

Built once per parsed repository: each commit's facts are pre-flattened into
an aggregated {object -> (added, removed)} map (paths interned by RepoData).
Queries only re-aggregate over the selected commit set — changing filters
never re-parses the repo or re-walks git.

Author identity goes through a resolver so mailmap / manual merges (T6)
plug in without touching this code.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .parse import RepoData

ROOT = ""
DAY = 86400
_DAY_MAX_DAYS = 120    # spans above this use week buckets
_WEEK_MAX_DAYS = 400   # spans above this use month buckets


def default_resolver(name: str, email: str, _aid: int) -> tuple[str, str, str]:
    """Canonical (key, name, email) = raw pair (T6 substitutes mailmap/merges)."""
    return f"{name} <{email}>", name, email


class MetricsEngine:
    def __init__(self, data: RepoData, author_resolver=None):
        self.data = data
        self.commits = data.commits
        self._resolver = author_resolver or default_resolver

        # object 0 is the root directory; dirs/files interned from commit facts
        self.objects: list[tuple[str, str]] = [("dir", ROOT)]  # id -> (kind, path)
        self.dirs: dict[str, int] = {ROOT: 0}
        self.files: dict[str, int] = {}

        # raw author id -> (key, name, email); key -> (name, email) first seen
        self.author_meta: list[tuple[str, str, str]] = []
        self.key_meta: dict[str, tuple[str, str]] = {}
        for aid, (name, email) in enumerate(data.authors):
            key, cname, cemail = self._resolver(name, email, aid)
            self.author_meta.append((key, cname, cemail))
            self.key_meta.setdefault(key, (cname, cemail))

        # per commit: {obj_id: (added, removed)} with lambda > 0 only
        self._touch: list[dict[int, tuple[int, int]]] = []
        self._build()

    # ------------------------------------------------------------------
    # registration + fact flattening
    # ------------------------------------------------------------------

    def _intern_dir(self, path: str) -> int:
        oid = self.dirs.get(path)
        if oid is None:
            oid = len(self.objects)
            self.objects.append(("dir", path))
            self.dirs[path] = oid
        return oid

    def _intern_file(self, path: str) -> int:
        oid = self.files.get(path)
        if oid is None:
            oid = len(self.objects)
            self.objects.append(("file", path))
            self.files[path] = oid
        return oid

    def _build(self) -> None:
        """Flatten per-commit facts and roll each up onto ancestors + root."""
        paths = self.data.paths
        for commit in self.commits:
            touch: dict[int, tuple[int, int]] = {}
            for path_id, added, removed in commit.facts:
                path = paths[path_id]
                parts = path.split("/")
                targets = [self._intern_file(path), 0]  # the file itself + root
                for i in range(1, len(parts)):          # every ancestor directory
                    targets.append(self._intern_dir("/".join(parts[:i]) + "/"))
                if added == 0 and removed == 0:
                    continue  # pure rename / no-op: object exists, no metrics
                for oid in targets:
                    pa, pr = touch.get(oid, (0, 0))
                    touch[oid] = (pa + added, pr + removed)
            self._touch.append(touch)

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------

    def select_commit_indices(self, commits: dict | None = None,
                              author_keys=None) -> list[int]:
        """Resolve filters -> indices into self.commits (git order, newest first).

        commits: {"mode": "all"|"range"|"list", "start", "end", "hashes"}.
        Range semantics (PLAN §4): start <= ts, ts < end.
        author_keys: canonical keys; None or [] means no author filter.
        """
        commits = commits or {}
        mode = commits.get("mode") or "all"
        sel: list[int] = []

        if mode == "list":
            wanted = [h.strip().lower() for h in (commits.get("hashes") or [])
                      if h and h.strip()]
            if wanted:
                for i, c in enumerate(self.commits):
                    ch = c.hash.lower()
                    if any(ch.startswith(w) or w.startswith(ch) for w in wanted):
                        sel.append(i)
        else:
            start = commits.get("start")
            end = commits.get("end")
            for i, c in enumerate(self.commits):
                if mode == "range":
                    if start is not None and c.ts < start:
                        continue
                    if end is not None and c.ts >= end:
                        continue
                sel.append(i)

        if author_keys:
            keys = set(author_keys)
            sel = [i for i in sel
                   if self.author_meta[self.commits[i].author_id][0] in keys]
        return sel

    def _obj_id_for(self, path) -> int:
        path = (path or "").strip()
        if path in ("", ".", "/"):
            return 0
        if path.startswith("./"):
            path = path[2:]
        path = path.lstrip("/")
        oid = self.dirs.get(path)
        if oid is None:
            oid = self.dirs.get(path + "/")
        if oid is None:
            oid = self.files.get(path)
        if oid is None:
            raise KeyError(f"unknown path: {path!r}")
        return oid

    def _subtree(self, obj_id: int) -> tuple[list[int], list[int]]:
        """(dir_ids, file_ids) inside the object's subtree; dirs include self."""
        kind, path = self.objects[obj_id]
        if kind == "file":
            return [], [obj_id]
        if path == ROOT:
            return list(self.dirs.values()), list(self.files.values())
        return (
            [i for p, i in self.dirs.items() if p.startswith(path)],
            [i for p, i in self.files.items() if p.startswith(path)],
        )

    # ------------------------------------------------------------------
    # query
    # ------------------------------------------------------------------

    def query(self, path: str = "", commits: dict | None = None,
              author_keys=None) -> dict:
        """Aggregate the selected commit set for the selected object.

        Returns {commit_set_size, object, totals, files[], dirs[],
                 authors[], series[]} per the frozen API surface (PLAN §7).
        """
        sel = self.select_commit_indices(commits, author_keys)
        obj_id = self._obj_id_for(path)
        h_size = len(sel)

        stats: dict[int, list[int]] = {}     # obj_id -> [added, removed, n]
        authors: dict[str, list[int]] = {}   # key -> [commits, modifications, churn]
        buckets: dict[str, list[int]] = {}   # bucket -> [added, removed, churn, commits]
        unit = self._bucket_unit([self.commits[i].ts for i in sel])

        t_added = t_removed = t_n = 0
        for i in sel:
            c = self.commits[i]
            touch = self._touch[i]

            arow = authors.setdefault(self.author_meta[c.author_id][0], [0, 0, 0])
            arow[0] += 1

            dt = touch.get(obj_id)
            if dt is not None:
                t_added += dt[0]
                t_removed += dt[1]
                t_n += 1
                arow[1] += 1
                arow[2] += dt[0] + dt[1]

            for oid, (pa, pr) in touch.items():
                s = stats.setdefault(oid, [0, 0, 0])
                s[0] += pa
                s[1] += pr
                s[2] += 1

            brow = buckets.setdefault(self._bucket_key(c.ts, unit), [0, 0, 0, 0])
            brow[3] += 1
            if dt is not None:
                brow[0] += dt[0]
                brow[1] += dt[1]
                brow[2] += dt[0] + dt[1]

        dir_ids, file_ids = self._subtree(obj_id)
        file_rows = self._rows(file_ids, stats, h_size,
                               sort_key=lambda r: (-r["churn"], r["path"]))
        dir_rows = self._rows(dir_ids, stats, h_size,
                              sort_key=lambda r: r["path"])

        total_churn = t_added + t_removed
        author_rows = []
        for key, (n_commits, n_mods, churn) in authors.items():
            name, email = self.key_meta.get(key, (key, ""))
            author_rows.append({
                "key": key, "name": name, "email": email,
                "commits": n_commits, "modifications": n_mods, "churn": churn,
                "ownership": (churn / total_churn) if total_churn else 0.0,
            })
        author_rows.sort(key=lambda r: (-r["churn"], r["key"]))

        return {
            "commit_set_size": h_size,
            "object": self.objects[obj_id][1],
            "totals": self._metrics(t_added, t_removed, t_n, h_size),
            "files": file_rows,
            "dirs": dir_rows,
            "authors": author_rows,
            "series": [
                {"bucket": b, "added": v[0], "removed": v[1],
                 "churn": v[2], "commits": v[3]}
                for b, v in sorted(buckets.items())
            ],
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _metrics(added: int, removed: int, n: int, h_size: int) -> dict:
        churn = added + removed
        return {
            "added": added, "removed": removed, "growth": added - removed,
            "churn": churn, "modifications": n,
            "frequency": (n / h_size) if h_size else 0.0,
            "churn_rate": (churn / h_size) if h_size else 0.0,
        }

    def _rows(self, obj_ids, stats, h_size, sort_key) -> list[dict]:
        rows = []
        for oid in obj_ids:
            a, r, n = stats.get(oid, (0, 0, 0))
            row = self._metrics(a, r, n, h_size)
            row["path"] = self.objects[oid][1]
            rows.append(row)
        rows.sort(key=sort_key)
        return rows

    @staticmethod
    def _bucket_unit(tss: list[int]) -> str:
        if not tss:
            return "day"
        span = max(tss) - min(tss)
        if span > _WEEK_MAX_DAYS * DAY:
            return "month"
        if span > _DAY_MAX_DAYS * DAY:
            return "week"
        return "day"

    @staticmethod
    def _bucket_key(ts: int, unit: str) -> str:
        d = datetime.fromtimestamp(ts, tz=timezone.utc)
        if unit == "month":
            return d.strftime("%Y-%m")
        if unit == "week":
            return d.strftime("%G-W%V")
        return d.strftime("%Y-%m-%d")
