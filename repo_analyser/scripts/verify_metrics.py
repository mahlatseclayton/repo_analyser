#!/usr/bin/env python3
"""Verification for the metrics engine + API (PLAN.md §5/§7) — T8.

Sections:
  1. parse: fixture per-commit facts vs the hand table (§5)
  2. engine: fixture §5 table — all / author filter / manual list / range
  3. API: fixture zip upload, then POST /metrics reproduces §5 via Flask
  4. ground truth: cJSON vs independent `git log --numstat` sums
     (3 commit sets, rename commit, delete commit with per-path attribution)
  5. cJSON smoke: build + root query timing and invariants

Run:  .venv/bin/python scripts/verify_metrics.py
Exit code 0 = all checks pass.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.engine.metrics import MetricsEngine  # noqa: E402
from server.engine.parse import parse_repo       # noqa: E402
from server.main import app as flask_app         # noqa: E402

FIXTURE = ROOT / "data/fixtures/unit_repo"
FIXTURE_ZIP = ROOT / "data/fixtures/unit_repo.zip"
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


# ---------------------------------------------------------------------------
# §5 scenario battery, shared by the engine and the API (same assertions)
# ---------------------------------------------------------------------------

def assert_scenarios(prefix: str, q, expected: dict, exp_commits: list) -> None:
    """q(payload) -> result dict. payload uses the API shape ({path, commits, authorKeys})."""
    exp_all = expected["all"]
    res = q({})
    check(f"{prefix}: all |H|", res["commit_set_size"] == exp_all["commit_set_size"],
          f"{res['commit_set_size']} vs {exp_all['commit_set_size']}")
    check_metrics(f"{prefix}: all totals", res["totals"], exp_all["totals"])
    for path, want in exp_all["dirs"].items():
        label = f"{prefix}: dir {path or '<root>'!r}"
        row = row_by_path(res["dirs"], path)
        check(f"{label} present", row is not None)
        if row:
            check_metrics(label, row, want)
    got_files = {r["path"] for r in res["files"]}
    check(f"{prefix}: file set == touched paths", got_files == set(exp_all["files"]),
          f"got {sorted(got_files)}")
    for path, want in exp_all["files"].items():
        row = row_by_path(res["files"], path)
        if row:
            check_metrics(f"{prefix}: file {path}", row, want)
    got_authors = {a["key"]: a for a in res["authors"]}
    check(f"{prefix}: author set", set(got_authors) == set(exp_all["authors"]),
          f"got {sorted(got_authors)}")
    for key, want in exp_all["authors"].items():
        label = f"{prefix}: author {key}"
        row = got_authors.get(key)
        check(f"{label} present", row is not None)
        if row:
            check_metrics(label, row, want)
            n_commits = sum(1 for c in exp_commits if c["author"] == key)
            check(f"{label}.commits", row["commits"] == n_commits,
                  f"got {row['commits']} want {n_commits}")
    check(f"{prefix}: series churn == totals churn",
          sum(s["churn"] for s in res["series"]) == res["totals"]["churn"])
    check(f"{prefix}: series commits == |H|",
          sum(s["commits"] for s in res["series"]) == res["commit_set_size"])

    bob = q({"authorKeys": ["Bob <bob@test>"]})
    exp_bob = expected["author_filter_Bob"]
    check(f"{prefix}: bob |H|", bob["commit_set_size"] == exp_bob["commit_set_size"],
          f"{bob['commit_set_size']} vs {exp_bob['commit_set_size']}")
    check_metrics(f"{prefix}: bob totals", bob["totals"], exp_bob["totals"])
    check(f"{prefix}: bob authors == [Bob]",
          [a["key"] for a in bob["authors"]] == ["Bob <bob@test>"],
          f"got {[a['key'] for a in bob['authors']]}")
    if bob["authors"]:
        check_metrics(f"{prefix}: bob author row", bob["authors"][0],
                      {"churn": 8, "modifications": 2, "ownership": 1.0})

    hashes = [exp_commits[0]["hash"], exp_commits[3]["hash"]]
    manual = q({"commits": {"mode": "list", "hashes": hashes}})
    exp_manual = expected["manual_list_c1_c4"]
    check(f"{prefix}: manual |H|", manual["commit_set_size"] == exp_manual["commit_set_size"],
          f"{manual['commit_set_size']} vs {exp_manual['commit_set_size']}")
    check_metrics(f"{prefix}: manual totals", manual["totals"], exp_manual["totals"])

    rng = q({"commits": {"mode": "range", "start": exp_commits[2]["ts"]}})
    exp_rng = expected["range_from_c3"]
    check(f"{prefix}: range |H|", rng["commit_set_size"] == exp_rng["commit_set_size"],
          f"{rng['commit_set_size']} vs {exp_rng['commit_set_size']}")
    check_metrics(f"{prefix}: range totals", rng["totals"], exp_rng["totals"])


# ---------------------------------------------------------------------------
# Independent ground truth helpers (plain non -z git output, summed like awk)
# ---------------------------------------------------------------------------

def numstat_totals(git_dir: Path, hashes: list[str]) -> tuple[int, int, int, list[tuple[str, int, int]]]:
    """Sum l+/l- over exactly `hashes` from a fresh `git log --numstat` call."""
    proc = subprocess.run(
        ["git", "-C", str(git_dir), "log", "--no-merges", "-M50%", "--numstat",
         "--format=@@%H", "--no-walk", *hashes],
        capture_output=True, text=True,
    )
    added = removed = commits = 0
    entries: list[tuple[str, int, int]] = []
    for line in proc.stdout.splitlines():
        if line.startswith("@@"):
            commits += 1
            continue
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            a, r = int(parts[0]), int(parts[1])
            added += a
            removed += r
            entries.append((parts[2], a, r))
    return added, removed, commits, entries


def filter_commits(git_dir: Path, diff_filter: str, cap: int = 25) -> list[str]:
    """Up to `cap` non-merge commit hashes touching diff_filter ('R', 'D', ...)."""
    proc = subprocess.run(
        ["git", "-C", str(git_dir), "log", "--no-merges", f"--diff-filter={diff_filter}",
         "-M50%", "--format=%H", "HEAD"],
        capture_output=True, text=True,
    )
    return proc.stdout.split()[:cap]


# ---------------------------------------------------------------------------

def main() -> int:
    if not FIXTURE.exists() or not EXPECTED_FILE.exists():
        print("fixture missing - run: bash scripts/make_test_repo.sh")
        return 2
    expected = json.loads(EXPECTED_FILE.read_text())
    exp_commits = expected["commits"]  # oldest first (c1..c5)

    print("== 1. parse: per-commit facts (PLAN.md §5) ==")
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

    print("== 2. engine: §5 battery (all / bob / manual / range) ==")

    def engine_query(payload: dict) -> dict:
        return engine.query(
            path=payload.get("path") or "",
            commits=payload.get("commits") or {"mode": "all"},
            author_keys=payload.get("authorKeys") or [],
        )

    assert_scenarios("engine", engine_query, expected, exp_commits)

    print("== 3. API: fixture zip upload + POST /metrics ≡ §5 ==")
    if not FIXTURE_ZIP.exists():
        print("  (zip missing - run: bash scripts/make_test_repo.sh)")
        return 2
    client = flask_app.test_client()
    with open(FIXTURE_ZIP, "rb") as fh:
        resp = client.post("/api/repos", data={"file": (fh, "unit_repo.zip")})
    check("api upload -> 201 ready",
          resp.status_code == 201 and resp.get_json().get("status") == "ready",
          f"status {resp.status_code}")
    rid = resp.get_json().get("id") if resp.status_code == 201 else None
    if rid:
        def api_query(payload: dict) -> dict:
            r = client.post(f"/api/repos/{rid}/metrics", json=payload)
            if r.status_code != 200:
                check("api metrics HTTP 200", False,
                      f"payload={payload} -> {r.status_code} {r.get_data(as_text=True)[:120]}")
                return {}
            return r.get_json()

        try:
            assert_scenarios("api", api_query, expected, exp_commits)

            sub = api_query({"path": "sub/"})
            check("api: path sub/ totals", sub["totals"]["added"] == 2 and sub["totals"]["churn"] == 4)
            check("api: path sub/ files", [r["path"] for r in sub["files"]] == ["sub/b.txt"])
            r400 = client.post(f"/api/repos/{rid}/metrics", json={"path": "nope/"})
            check("api: unknown path -> 400", r400.status_code == 400, f"status {r400.status_code}")

            tree = client.get(f"/api/repos/{rid}/tree").get_json()
            check("api: tree has 9 rows", tree["count"] == 9, str(tree["count"]))
            cj = client.get(f"/api/repos/{rid}/commits").get_json()
            check("api: commits list 5 newest-first",
                  cj["total"] == 5 and cj["commits"][0]["hash"] == exp_commits[4]["hash"])
        finally:
            d = client.delete(f"/api/repos/{rid}")
            check("api: cleanup delete", d.status_code == 200, str(d.status_code))
    else:
        check("api: upload produced an id", False, resp.get_data(as_text=True)[:200])

    if CJSON.exists():
        print("== 4. cJSON: build + independent ground truth vs git --numstat ==")
        t0 = time.time()
        big = parse_repo(CJSON)
        eng2 = MetricsEngine(big)
        t_build = time.time() - t0
        print(f"  parsed {len(big.commits)} commits / {len(big.paths)} paths in {t_build:.2f}s")

        rev = subprocess.run(
            ["git", "-C", str(CJSON), "rev-list", "--no-merges", "HEAD"],
            capture_output=True, text=True,
        ).stdout.split()
        check("rev-list count == parsed commits", len(rev) == len(big.commits),
              f"{len(rev)} vs {len(big.commits)}")

        sets = {"newest 100": rev[:100], "middle 100": rev[300:400], "oldest 50": rev[-50:]}
        for label, hs in sets.items():
            res = eng2.query(commits={"mode": "list", "hashes": hs})
            a, r, _n, _e = numstat_totals(CJSON, hs)
            check(f"gt {label}: |H|", res["commit_set_size"] == len(hs),
                  f"{res['commit_set_size']} vs {len(hs)}")
            check(f"gt {label}: l+ == numstat", res["totals"]["added"] == a,
                  f"{res['totals']['added']} vs {a}")
            check(f"gt {label}: l- == numstat", res["totals"]["removed"] == r,
                  f"{res['totals']['removed']} vs {r}")

        rens = filter_commits(CJSON, "R")
        if rens:
            pure = None
            for h in rens:
                a, r, _n, _e = numstat_totals(CJSON, [h])
                if a + r == 0:
                    pure = h
                    break
            target = pure or rens[0]
            res = eng2.query(commits={"mode": "list", "hashes": [target]})
            a, r, _n, _e = numstat_totals(CJSON, [target])
            check("gt rename: |H| == 1", res["commit_set_size"] == 1)
            check("gt rename: l+/l- match",
                  (res["totals"]["added"], res["totals"]["removed"]) == (a, r),
                  f"{res['totals']['added']}/{res['totals']['removed']} vs {a}/{r}")
            check("gt rename: n invariant",
                  res["totals"]["modifications"] == (1 if a + r else 0),
                  f"n={res['totals']['modifications']} churn={a + r}")
            print(f"  rename commit checked: {target[:10]} (pure={bool(pure)})")
        else:
            print("  (no rename commits found - skipped)")

        dels = filter_commits(CJSON, "D")
        if dels:
            target = dels[0]
            res = eng2.query(commits={"mode": "list", "hashes": [target]})
            a, r, _n, entries = numstat_totals(CJSON, [target])
            check("gt delete: |H| == 1", res["commit_set_size"] == 1)
            check("gt delete: l+/l- match",
                  (res["totals"]["added"], res["totals"]["removed"]) == (a, r),
                  f"{res['totals']['added']}/{res['totals']['removed']} vs {a}/{r}")
            if a == 0 and r > 0:
                check("gt delete: only commit counts as modification",
                      res["totals"]["modifications"] == 1, str(res["totals"]["modifications"]))
            mismatched = 0
            considered = 0
            for p, pa, pr in entries:
                if " => " in p or "{" in p:
                    continue  # rename-ish path annotation; attribution checked below
                considered += 1
                row = row_by_path(res["files"], p)
                if row is None or row["added"] != pa or row["removed"] != pr:
                    mismatched += 1
            check("gt delete: per-path attribution", mismatched == 0,
                  f"{mismatched}/{considered} paths mismatched")
            print(f"  delete commit checked: {target[:10]} ({considered} paths)")
        else:
            print("  (no delete commits found - skipped)")

        print("== 5. cJSON smoke: root query ==")
        t1 = time.time()
        r2 = eng2.query()
        t_query = time.time() - t1
        check("cjson |H| == parsed commits", r2["commit_set_size"] == len(big.commits))
        check("cjson series churn == totals churn",
              sum(s["churn"] for s in r2["series"]) == r2["totals"]["churn"])
        own = sum(a["ownership"] for a in r2["authors"])
        check("cjson ownership sums to 1", abs(own - 1.0) <= 1e-9, f"got {own}")
        print(f"  cJSON root ({t_query:.3f}s): |H|={r2['commit_set_size']} "
              f"l+={r2['totals']['added']} l-={r2['totals']['removed']} "
              f"lambda={r2['totals']['churn']} n={r2['totals']['modifications']} "
              f"authors={len(r2['authors'])}")
    else:
        print("== 4/5. cJSON ground truth + smoke: skipped (mirror not present) ==")

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
