#!/usr/bin/env bash
# Build the deterministic unit-test fixture repo (PLAN.md §5) and emit
# scripts/fixture_expected.json with the hand-computed expected metrics.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$ROOT/data/fixtures/unit_repo"
ZIP="$ROOT/data/fixtures/unit_repo.zip"

rm -rf "$REPO" "$ZIP"
mkdir -p "$REPO"
cd "$REPO"

git init -q -b main
T0=1700000000

commit_as() { # name email message offset-seconds
  local name="$1" email="$2" msg="$3" off="$4" d
  d="@$((T0 + off)) +0000"
  GIT_AUTHOR_NAME="$name" GIT_AUTHOR_EMAIL="$email" \
  GIT_COMMITTER_NAME="$name" GIT_COMMITTER_EMAIL="$email" \
  GIT_AUTHOR_DATE="$d" GIT_COMMITTER_DATE="$d" \
  git commit -q -m "$msg"
}

# c1 — Alice: a.txt (3 lines), bin.dat (binary, must be ignored), sub/b.txt (2), pkg/x.py (1)
printf 'a\nb\nc\n' > a.txt
printf '\000\001\002bin' > bin.dat
mkdir -p sub pkg
printf 'x\ny\n' > sub/b.txt
printf 'q\n' > pkg/x.py
git add -A
commit_as Alice alice@test c1 0

# c2 — Bob: a.txt edit (numstat 3/1), delete sub/b.txt (0/2)
printf 'a\nB\nc\nd\ne\n' > a.txt
rm sub/b.txt
git add -A
commit_as Bob bob@test c2 3600

# c3 — Alice: pure rename a.txt -> a2.txt (0/0, no metric change)
git mv a.txt a2.txt
commit_as Alice alice@test c3 7200

# c4 — Bob: rename + edit a2.txt -> a3.txt (numstat 2/0, attributed to new path)
printf 'a\nB\nc\nd\ne\nf\ng\n' > a2.txt
git mv a2.txt a3.txt
git add -A
commit_as Bob bob@test c4 10800

# c5 — Alice: pure directory rename pkg/ -> pkg2/ (0/0)
git mv pkg pkg2
commit_as Alice alice@test c5 14400

# Zip a copy for ingestion tests (contains .git at the top level)
python3 - "$REPO" "$ZIP" <<'PY'
import os, shutil, sys
root, out = sys.argv[1], sys.argv[2]
shutil.make_archive(out[:-4], "zip", root_dir=root)
print(f"zip written: {out} ({os.path.getsize(out)} bytes)")
PY

# Emit expected metrics JSON (hand-computed per PLAN.md §5)
python3 - "$REPO" "$ROOT/scripts/fixture_expected.json" <<'PY'
import json, subprocess, sys
repo, out = sys.argv[1], sys.argv[2]
log = subprocess.run(
    ["git", "-C", repo, "log", "--reverse", "--format=%H|%ct|%an|%ae", "HEAD"],
    capture_output=True, text=True, check=True,
).stdout.strip().splitlines()
commits = []
for line in log:
    h, ts, name, email = line.split("|")
    commits.append({"hash": h, "ts": int(ts), "author": f"{name} <{email}>"})

expected = {
    "commits": commits,
    "all": {
        "commit_set_size": 5,
        "totals": {"added": 11, "removed": 3, "growth": 8, "churn": 14,
                   "modifications": 3, "frequency": 0.6, "churn_rate": 2.8},
        "dirs": {
            "":      {"added": 11, "removed": 3, "growth": 8, "churn": 14,
                      "modifications": 3, "frequency": 0.6, "churn_rate": 2.8},
            "pkg/":  {"added": 1, "removed": 0, "growth": 1, "churn": 1,
                      "modifications": 1, "frequency": 0.2, "churn_rate": 0.2},
            "pkg2/": {"added": 0, "removed": 0, "growth": 0, "churn": 0,
                      "modifications": 0, "frequency": 0.0, "churn_rate": 0.0},
            "sub/":  {"added": 2, "removed": 2, "growth": 0, "churn": 4,
                      "modifications": 2, "frequency": 0.4, "churn_rate": 0.8},
        },
        "files": {
            "a.txt":     {"added": 3, "removed": 1, "growth": 2, "churn": 4, "modifications": 2},
            "a2.txt":    {"added": 0, "removed": 0, "growth": 0, "churn": 0, "modifications": 0},
            "a3.txt":    {"added": 2, "removed": 0, "growth": 2, "churn": 2, "modifications": 1},
            "pkg/x.py":  {"added": 1, "removed": 0, "growth": 1, "churn": 1, "modifications": 1},
            "pkg2/x.py": {"added": 0, "removed": 0, "growth": 0, "churn": 0, "modifications": 0},
            "sub/b.txt": {"added": 2, "removed": 2, "growth": 0, "churn": 4, "modifications": 2},
        },
        "binary_excluded": ["bin.dat"],
        "authors": {
            "Alice <alice@test>": {"churn": 6, "modifications": 1, "ownership": 6 / 14},
            "Bob <bob@test>":     {"churn": 8, "modifications": 2, "ownership": 8 / 14},
        },
    },
    "author_filter_Bob": {
        "commit_set_size": 2,
        "totals": {"added": 5, "removed": 3, "growth": 2, "churn": 8,
                   "modifications": 2, "frequency": 1.0, "churn_rate": 4.0},
    },
    "manual_list_c1_c4": {
        "commit_set_size": 2,
        "totals": {"added": 8, "removed": 0, "growth": 8, "churn": 8,
                   "modifications": 2, "frequency": 1.0, "churn_rate": 4.0},
    },
    "range_from_c3": {
        "commit_set_size": 3,
        "totals": {"added": 2, "removed": 0, "growth": 2, "churn": 2,
                   "modifications": 1, "frequency": 1 / 3, "churn_rate": 2 / 3},
    },
}
with open(out, "w") as fh:
    json.dump(expected, fh, indent=2)
print(f"expected written: {out}")
PY

echo "--- fixture log (newest first) ---"
git -C "$REPO" log --oneline
echo "fixture ready at $REPO"
