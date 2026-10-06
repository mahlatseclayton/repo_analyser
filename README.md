# RAT — Repo Analysis Tool

Multi-repo web dashboard that ingests Git repositories (zip with `.git`, or clone
URL) and computes the metric set from the COMS3011A brief: per file, per directory,
per repository, per commit set and per author.

## Quick start

```bash
# backend (Python 3.12 + Flask)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server/main.py            # http://127.0.0.1:8000

# frontend (Node 18+)
cd web
npm install
npm run dev                                # dev server :5173, proxies /api → :8000

# or build once and let Flask serve it (single origin):
npm run build                              # writes web/dist, served by Flask
```

Open http://127.0.0.1:5173 (dev) or http://127.0.0.1:8000 (built).

## Using the tool

1. **Add a repository** — sidebar: paste a clone URL (`https://…`, `git@…`) or drop
   a zip that contains the repo's `.git` directory (root or one level down). Clone /
   extraction status is polled live.
2. **Filter** — filter bar: pick authors (multi-select), a file or directory
   (searchable path picker), and the commit set: All / Time range (inclusive dates) /
   Manual (search + checkboxes + select-all-shown).
3. **Dashboard** — **Overview** (stat cards `l+`, `l−`, `δ`, `λ`, `n`, `η`, `ρ`, `|H|`;
   activity-over-time chart; top-files-by-churn chart), **Files & Dirs** (sortable
   tables), **Authors** (ownership pie + sortable table). All views follow the filters
   and refresh with a debounce.
4. **Merge authors** — the "Merge authors" button opens a modal: select identities,
   group them, choose the canonical one, save. Merges persist per repo and refresh
   every view. `.mailmap` files are applied automatically at load time.

## Architecture

```
server/main.py            Flask API (JSON) + serves the built frontend
server/storage.py         repo registry (data/repos.json), zip extraction, clone, status
server/engine/parse.py    one streaming `git log --numstat -z` pass per repo → facts
server/engine/metrics.py  per-commit ancestor-dir rollups, commit-set/author queries
server/engine/authors.py  .mailmap parsing (git's four forms) + manual merge groups
server/service.py         per-repo cache: parse once; memoized query results
web/                      Vite + React 18 + Recharts single-page app
scripts/make_test_repo.sh hand-built fixture repo (5 commits, two authors)
scripts/verify_metrics.py 267-check verification suite (spec fixture + cJSON ground truth)
data/                     runtime data (gitignored): registry, uploads, clones
```

Performance choices: a **single git pass per repo** (no per-commit subprocesses),
memory-efficient handling of merges/renames/binary files, per-commit ancestor-dir
aggregation so any query is a rollup over the selected commit set, RAM caching of
parsed facts, memoized queries keyed by (repo, merge-version, filters), and
background clone with polled status. cJSON (955 commits) parses in ≈0.25 s; queries
answer in milliseconds after the first load.

The metric semantics match the brief: non-merge commits reachable from HEAD,
committer dates, `numstat` line counts, binary files excluded, renames attributed to
the new path, `λ = l+ + l−`, `n` = commits with `λ > 0` on the object,
`η = n/|H|`, `ρ = λ/|H|`, author `ω = λ_a/λ`; directory metrics are recursive sums;
time ranges are `start ≤ ts < end`.

## Verification

```bash
.venv/bin/python scripts/make_test_repo.sh     # rebuild the fixture (data/fixtures/)
.venv/bin/python scripts/verify_metrics.py     # → RESULT: all 267 checks passed
```

`verify_metrics.py` asserts the fixture matches the hand-computed spec table exactly
(both through the engine and the HTTP API), and independently cross-checks three
commit ranges of the cJSON repository against raw `git log --numstat` sums,
including a pure-rename commit and a delete-only commit. Every API number shown in
the UI comes from this verified surface.

```bash
# grading-reference check: every row of repo-references/cJSON_*.csv
# (repository + all files/dirs + all author rows) at the pinned commit:
.venv/bin/python scripts/verify_references.py cJSON   # → all 7172 checks passed
```

## AI declaration

This project was developed with the assistance of an AI coding agent/Qoder. The human
operator defined the plan of record (`PLAN.md`), reviewed and manually tested every
increment, and approved each change; the agent generated code and ran the automated
verification suite (`scripts/verify_metrics.py`). Correctness claims in this README
are backed by that suite.
