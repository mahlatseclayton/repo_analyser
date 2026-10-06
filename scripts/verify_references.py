#!/usr/bin/env python3
"""Verify the engine against the three grading reference CSVs (repo-references/).

Each CSV holds ground truth for one repo at a pinned commit:
  repo, ref_sha, commit_set, commit_count, object_type, path, author,
  added, removed, growth, churn, modifications,
  modification_frequency, churn_rate, ownership

Column mapping to our metric names: added=l+, removed=l-, growth=delta,
churn=lambda, modifications=n, modification_frequency=eta, churn_rate=rho,
ownership=omega. Rows: one ALL row per object (repository "/" / directory /
file) plus one row per author who modified the object (lambda_a > 0).

For each repo: ensure HEAD is the pinned ref (pins a local `rat-ref` branch
when needed), parse once, aggregate per-object and per-(object, author)
stats in a single pass, then compare every CSV row and check object/author
coverage in both directions.

Run:  .venv/bin/python scripts/verify_references.py [cJSON|redis|git ...]
Exit code 0 = all present repos match every row.
"""
from __future__ import annotations

import csv
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.engine.metrics import MetricsEngine  # noqa: E402
from server.engine.parse import parse_repo       # noqa: E402

TOL = 1e-9  # relative tolerance for eta/rho/omega (both sides are IEEE doubles)

REPOS = [
    ("cJSON", "6d9f2443ab071f86e5d9b43025a40929ec41c46c",
     ROOT / "repo-references/cJSON_6d9f2443ab07.csv",
     ROOT / "data/testrepos/cjson.git"),
    ("redis", "b540ca49cba815f3fbe634363c3df68d4f4f127a",
     ROOT / "repo-references/redis_b540ca49cba8.csv",
     ROOT / "data/testrepos/redis.git"),
    ("git", "5a7d1e8045ce66c908f62598e26cbb8df7b39a90",
     ROOT / "repo-references/git_5a7d1e8045ce.csv",
     ROOT / "data/testrepos/git.git"),
]


def _git(git_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(git_dir), *args],
                          capture_output=True, text=True)


def head_sha(git_dir: Path) -> str | None:
    proc = _git(git_dir, "rev-parse", "HEAD")
    return proc.stdout.strip() if proc.returncode == 0 else None


def pin_head(git_dir: Path, sha: str) -> bool:
    """Point HEAD at `sha` via a local rat-ref branch (parse_repo reads HEAD)."""
    if _git(git_dir, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        return False
    if (_git(git_dir, "update-ref", "refs/heads/rat-ref", sha).returncode != 0
            or _git(git_dir, "symbolic-ref", "HEAD",
                    "refs/heads/rat-ref").returncode != 0):
        return False
    return head_sha(git_dir) == sha


class Tally:
    def __init__(self) -> None:
        self.checks = 0
        self.failures: list[str] = []

    def check(self, name: str, got, want) -> None:
        self.checks += 1
        if isinstance(got, int) and isinstance(want, int):
            ok = got == want
        else:
            try:
                ok = abs(float(got) - float(want)) <= TOL * max(1.0, abs(float(want)))
            except (TypeError, ValueError):
                ok = False
        if not ok:
            self.failures.append(f"{name}: got {got!r}, want {want!r}")

    def check_field(self, label: str, row: dict, col: str, got) -> None:
        raw = (row.get(col) or "").strip()
        if raw == "":
            return  # column not applicable for this row kind
        self.check(f"{label}.{col}", got, raw)


def build_stats(engine: MetricsEngine):
    """Single pass: obj -> [l+, l-, n] and (obj, author_key) -> [l+, l-, n]."""
    per_obj: dict[int, list[int]] = {}
    per_oa: dict[tuple[int, str], list[int]] = {}
    for commit, touch in zip(engine.commits, engine._touch):
        akey = engine.author_meta[commit.author_id][0]
        for oid, (added, removed) in touch.items():
            s = per_obj.get(oid)
            if s is None:
                per_obj[oid] = [added, removed, 1]
            else:
                s[0] += added
                s[1] += removed
                s[2] += 1
            t = per_oa.get((oid, akey))
            if t is None:
                per_oa[(oid, akey)] = [added, removed, 1]
            else:
                t[0] += added
                t[1] += removed
                t[2] += 1
    return per_obj, per_oa


def obj_id_for(engine: MetricsEngine, otype: str, path: str) -> int | None:
    if otype == "repository":
        return 0 if path == "/" else None
    if otype == "directory":
        return engine.dirs.get(path.rstrip("/") + "/")
    if otype == "file":
        return engine.files.get(path)
    return None


def verify_repo(name: str, sha: str, csv_path: Path, git_dir: Path,
                tally: Tally) -> bool:
    print(f"\n== {name} @ {sha[:12]} ==")
    if not git_dir.exists():
        print(f"  SKIP: {git_dir} not cloned yet")
        return False
    if head_sha(git_dir) != sha and not pin_head(git_dir, sha):
        print(f"  SKIP: commit {sha[:12]} not present in {git_dir}")
        return False

    t0 = time.perf_counter()
    engine = MetricsEngine(parse_repo(git_dir))
    per_obj, per_oa = build_stats(engine)
    h_size = len(engine.commits)
    parse_s = time.perf_counter() - t0

    # object inventory for coverage checks (path label per object id)
    labels = {0: "/"}
    for oid, (kind, path) in enumerate(engine.objects):
        if oid == 0:
            continue
        labels[oid] = path if kind == "file" else path.rstrip("/") + "/"

    csv_objects: set[str] = set()      # "type:label"
    csv_pairs: set[tuple[str, str]] = set()
    rows = 0
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows += 1
            otype, path, author = row["object_type"], row["path"], row["author"]
            oid = obj_id_for(engine, otype, path)
            label = f"{otype}:{path}"
            csv_objects.add(label)
            if oid is None:
                tally.failures.append(
                    f"{name}: CSV {label} not an object in our engine")
                continue

            # commit_count column is |H| for the whole analysis
            tally.check(f"{name} {label} commit_count", h_size,
                        int(row["commit_count"]))

            if author == "ALL":
                added, removed, n = per_obj.get(oid, (0, 0, 0))
                label2 = f"{name} {otype} {path} [ALL]"
                tally.check(f"{label2}.l+", added, int(row["added"]))
                tally.check(f"{label2}.l-", removed, int(row["removed"]))
                tally.check(f"{label2}.delta",
                            added - removed, int(row["growth"]))
                tally.check(f"{label2}.lambda",
                            added + removed, int(row["churn"]))
                tally.check(f"{label2}.n", n, int(row["modifications"]))
                tally.check_field(label2, row, "modification_frequency",
                                  n / h_size if h_size else 0.0)
                tally.check_field(label2, row, "churn_rate",
                                  (added + removed) / h_size if h_size else 0.0)
            else:
                key = (oid, author)
                added, removed, n = per_oa.get(key, (0, 0, 0))
                label2 = f"{name} {otype} {path} [{author}]"
                tally.check(f"{label2}.l+", added, int(row["added"]))
                tally.check(f"{label2}.l-", removed, int(row["removed"]))
                tally.check(f"{label2}.delta",
                            added - removed, int(row["growth"]))
                tally.check(f"{label2}.lambda",
                            added + removed, int(row["churn"]))
                tally.check(f"{label2}.n", n, int(row["modifications"]))
                obj_lambda = (per_obj.get(oid, (0, 0, 0))[0]
                              + per_obj.get(oid, (0, 0, 0))[1])
                tally.check_field(label2, row, "ownership",
                                  (added + removed) / obj_lambda
                                  if obj_lambda else 0.0)
                csv_pairs.add((label, author))

    # coverage: every engine object with lambda > 0 must be a CSV row
    # (CSV path convention: no trailing slash on directories)
    def csv_label(oid: int) -> str:
        if oid == 0:
            return "repository:/"
        kind, p = engine.objects[oid]
        return ("file:" + p) if kind == "file" else ("directory:" + p.rstrip("/"))

    ours = {csv_label(oid) for oid in per_obj if oid}
    missing = ours - csv_objects
    for label in sorted(missing)[:10]:
        tally.failures.append(f"{name}: object {label} (lambda>0) missing from CSV")
    if len(missing) > 10:
        tally.failures.append(
            f"{name}: ... and {len(missing) - 10} more objects missing from CSV")

    # coverage: every (object, author) with lambda_a > 0 must be a CSV row
    our_pairs = {(csv_label(oid), akey) for oid, akey in per_oa}
    missing_pairs = our_pairs - csv_pairs
    for label, akey in sorted(missing_pairs)[:10]:
        tally.failures.append(
            f"{name}: author row {akey} on {label} (lambda_a>0) missing from CSV")
    if len(missing_pairs) > 10:
        tally.failures.append(
            f"{name}: ... and {len(missing_pairs) - 10} more author rows missing")

    print(f"  parsed {h_size} commits in {parse_s:.1f}s; "
          f"{len(per_obj)} objects with lambda>0, {len(per_oa)} author-rows")
    print(f"  compared {rows} CSV rows "
          f"({len(csv_objects)} objects, {len(csv_pairs)} author-rows)")
    return True


def main() -> int:
    wanted = {a for a in sys.argv[1:] if not a.startswith("-")}
    tally = Tally()
    ran = 0
    for name, sha, csv_path, git_dir in REPOS:
        if wanted and name not in wanted:
            continue
        if verify_repo(name, sha, csv_path, git_dir, tally):
            ran += 1

    print(f"\n== RESULT ==")
    if ran == 0:
        print("no repos available to verify (clone data/testrepos first)")
        return 2
    if tally.failures:
        print(f"FAIL: {len(tally.failures)} of {tally.checks} checks failed:")
        for f in tally.failures[:60]:
            print(f"  - {f}")
        if len(tally.failures) > 60:
            print(f"  ... and {len(tally.failures) - 60} more")
        return 1
    print(f"RESULT: all {tally.checks} reference checks passed "
          f"({ran} repos verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
