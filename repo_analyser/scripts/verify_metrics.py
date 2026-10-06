#!/usr/bin/env python3
"""Fixture verification for the metrics engine (PLAN.md §5).

Asserts that MetricsEngine reproduces every hand-computed value in
scripts/fixture_expected.json / PLAN.md §5, plus engine invariants
(series sums, author ownership) and a cJSON smoke run.

Run:  .venv/bin/python scripts/verify_metrics.py
Exit code 0 = all checks pass.  (T8 extends this with API-level checks
and independent cJSON ground truth.)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.engine.metrics import MetricsEngine  # noqa: E402
from server.engine.parse import parse_repo       # noqa: E402

FIXTURE = ROOT / "data/fixtures/unit_repo"
CJSON = ROOT / "data/testrepos/cjson.git"
EXPECTED_FILE = ROOT / "scripts/fixture_expected.json"

# PLAN.md §5 per-commit numstat table (commit label -> facts).
EXPECTED_FACTS = {
    "c1": [("a.txt", 3, 0), ("pkg/x.py", 1, 0), ("sub/b.txt", 2, 0)],
    "c2": [("a.txt", 3, 1), ("sub/b.txt", 0, 2)],
    "c3": [("a2.txt", 0, 0)],
    "c4": [("a3.txt", 2, 0)],
    "c5": [("pkg2/x.py", 0, 0)],
}

failures: list[str] = []
checks = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    status = "PASS" if cond else "FAIL"
    suffix = f"  -- {detail}" if detail and not cond else ""
    print(f"  [{status}] {name}{suffix}")
    if not cond:
        failures.append(name)


def check_metrics(name: str, row: dict, wanted: dict) -> None:
    for key, want in wanted.items():
        got = row.get(key, "<missing>")
        ok = isinstance(got, (int, float)) and abs(float(got) - float(want)) <= 1e-9
        check(f"{name}.{key}", ok, f"got {got!r} want {want!r}")


def row_by_path(rows: list[dict], path: str) -> dict | None:
    return next((r for r in rows if r["path"] == path), None)


def main() -> int:
    if not FIXTURE.exists() or not EXPECTED_FILE.exists():
        print("fixture missing - run: bash scripts/make_test_repo.sh")
        return 2
    expected = json.loads(EXPECTED_FILE.read_text())
    exp_commits = expected["commits"]  # oldest first (c1..c5)

    print("== parse: per-commit facts (PLAN.md §5) ==")
    data = parse_repo(FIXTURE)
    engine = MetricsEngine(data)
    parsed = sorted(data.commits, key=lambda c: c.ts)
    check("commit count", len(parsed) == len(exp_commits),
          f"{len(parsed)} vs {len(exp_commits)}")
    for idx, exp_c in enumerate(exp_commits):
        label = f"c{idx + 1}"
        got_c = next((c for c in parsed if c.hash == exp_c["hash"]), None)
        check(f"{label} hash present", got_c is not None, exp_c["hash"])
        if got_c is None:
            continue
        got_facts = sorted((data.paths[pid], a, r) for pid, a, r in got_c.facts)
        check(f"{label} numstat facts", got_facts == sorted(EXPECTED_FACTS[label]),
              f"got {got_facts} want {sorted(EXPECTED_FACTS[label])}")
        check(f"{label} timestamp", got_c.ts == exp_c["ts"],
              f"{got_c.ts} vs {exp_c['ts']}")
    check("bin.dat never interned (binary excluded)", "bin.dat" not in data.paths)

    print("== all commits: totals / dirs / files / authors ==")
    res = engine.query()
    exp_all = expected["all"]
    check("commit_set_size", res["commit_set_size"] == exp_all["commit_set_size"],
          f"{res['commit_set_size']} vs {exp_all['commit_set_size']}")
    check_metrics("totals", res["totals"], exp_all["totals"])
    for path, want in exp_all["dirs"].items():
        label = f"dir {path or '<root>'!r}"
        row = row_by_path(res["dirs"], path)
        check(f"{label} present", row is not None)
        if row:
            check_metrics(label, row, want)
    got_files = {r["path"] for r in res["files"]}
    check("file set == touched paths", got_files == set(exp_all["files"]),
          f"got {sorted(got_files)} want {sorted(exp_all['files'])}")
    for path, want in exp_all["files"].items():
        row = row_by_path(res["files"], path)
        if row:
            check_metrics(f"file {path}", row, want)
    got_authors = {a["key"]: a for a in res["authors"]}
    check("author set", set(got_authors) == set(exp_all["authors"]),
          f"got {sorted(got_authors)}")
    for key, want in exp_all["authors"].items():
        label = f"author {key}"
        row = got_authors.get(key)
        check(f"{label} present", row is not None)
        if row:
            check_metrics(label, row, want)
            n_commits = sum(1 for c in exp_commits if c["author"] == key)
            check(f"{label}.commits", row["commits"] == n_commits,
                  f"got {row['commits']} want {n_commits}")
    check("series churn == totals churn",
          sum(s["churn"] for s in res["series"]) == res["totals"]["churn"])
    check("series commits == |H|",
          sum(s["commits"] for s in res["series"]) == res["commit_set_size"])

    print("== author filter: Bob ==")
    bob = engine.query(author_keys=["Bob <bob@test>"])
    exp_bob = expected["author_filter_Bob"]
    check("bob |H|", bob["commit_set_size"] == exp_bob["commit_set_size"],
          f"{bob['commit_set_size']} vs {exp_bob['commit_set_size']}")
    check_metrics("bob totals", bob["totals"], exp_bob["totals"])
    check("bob author rows == [Bob]",
          [a["key"] for a in bob["authors"]] == ["Bob <bob@test>"],
          f"got {[a['key'] for a in bob['authors']]}")
    if bob["authors"]:
        check_metrics("bob author row", bob["authors"][0],
                      {"churn": 8, "modifications": 2, "ownership": 1.0})

    print("== manual commit list {c1, c4} ==")
    hashes = [exp_commits[0]["hash"], exp_commits[3]["hash"]]
    manual = engine.query(commits={"mode": "list", "hashes": hashes})
    exp_manual = expected["manual_list_c1_c4"]
    check("manual |H|", manual["commit_set_size"] == exp_manual["commit_set_size"],
          f"{manual['commit_set_size']} vs {exp_manual['commit_set_size']}")
    check_metrics("manual totals", manual["totals"], exp_manual["totals"])

    print("== time range from c3 (ts >= c3.ts) ==")
    rng = engine.query(commits={"mode": "range", "start": exp_commits[2]["ts"]})
    exp_rng = expected["range_from_c3"]
    check("range |H|", rng["commit_set_size"] == exp_rng["commit_set_size"],
          f"{rng['commit_set_size']} vs {exp_rng['commit_set_size']}")
    check_metrics("range totals", rng["totals"], exp_rng["totals"])

    if CJSON.exists():
        print("== cJSON smoke: build + root query ==")
        t0 = time.time()
        big = parse_repo(CJSON)
        eng2 = MetricsEngine(big)
        t_build = time.time() - t0
        t1 = time.time()
        r2 = eng2.query()
        t_query = time.time() - t1
        print(f"  parsed {len(big.commits)} commits / {len(big.paths)} paths "
              f"in {t_build:.2f}s; root query in {t_query:.3f}s")
        check("cjson |H| == parsed commits", r2["commit_set_size"] == len(big.commits))
        check("cjson series churn == totals churn",
              sum(s["churn"] for s in r2["series"]) == r2["totals"]["churn"])
        own = sum(a["ownership"] for a in r2["authors"])
        check("cjson ownership sums to 1", abs(own - 1.0) <= 1e-9,
              f"got {own}")
        print(f"  cJSON root: |H|={r2['commit_set_size']} "
              f"l+={r2['totals']['added']} l-={r2['totals']['removed']} "
              f"lambda={r2['totals']['churn']} n={r2['totals']['modifications']} "
              f"authors={len(r2['authors'])}")
    else:
        print("== cJSON smoke: skipped (data/testrepos/cjson.git not present) ==")

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILED / {checks} checks")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"RESULT: all {checks} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
