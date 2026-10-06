# RAT — Repo Analysis Tool · Implementation Plan

> COMS3011A test · Target: working, verified product in ~60 min · Task granularity: ~5 min each
> **Status: awaiting your go-ahead — no build work starts until you approve this plan.**

---

## 1. What we're building (brief digest)

A **multi-repo web dashboard** ("RAT") that ingests a Git repo (zip with `.git`, or clone URL) and computes metrics for **each file, each directory, the repository, commit sets, and each author**, exactly as formally defined in the brief.

- **Ingestion:** zip upload (must contain `.git`) + remote URL (deep clone).
- **Filters:** repository, author, file/directory, commits (time period `H_t` / `H_i,j` or manually selected list).
- **Author merging:** automatic via `.mailmap` + manual merge UI.
- **Grading (50/25/25):** metric correctness validated against cJSON / redis / git.git samples at given commit hashes · efficient architecture + visualisation · usability (navigation, error handling, speed on ~100k-commit repos, QoL).

## 2. Strategy (order of work)

1. **Correctness first (50%)** — engine validated against a hand-computed fixture and against cJSON.
2. **Full feature set for top tier** — both ingestions, all filters, author merge, multi-repo.
3. **Architecture & visualisation (25%)** — single-pass git parse, rollup aggregation, background indexing, cache, real charts.
4. **Usability (25%)** — nav, error handling, empty/loading states, performance, QoL.

Task order below guarantees: if time runs short, only P2 polish gets cut (see "cut line").

## 3. Locked technical decisions

| Area | Decision | Why |
|---|---|---|
| Backend | Python 3.12 + **Flask** in project `.venv` (single pip package) | Fewest dependencies & version pitfalls (no ASGI, no pydantic); threaded dev server; serves API + static frontend |
| Frontend | Vite 5 + React 18 + Recharts 2, **exact versions pinned** in package.json (Node 18 verified) | Deterministic installs, plain JS (no TypeScript friction), real charts |
| Git access | `git` CLI via subprocess — **one `git log` pass per repo** | No per-commit subprocess overhead → performance tier |
| Storage | `data/repos.json` registry + `data/repos/<id>/`; parsed facts cached in RAM | Simple, restart-safe, instant re-queries |
| Repos | zip → extract to `data/repos/<id>/work/` (locate `.git` at root or 1 level down); URL → `git clone --mirror` → `data/repos/<id>/repo.git`, background thread + polled status | Handles real-world zips (nested repo folder) |

**Why this stack is low-error for a 1-hour build:** backend = 1 pip package (Flask) + Python stdlib (`subprocess`, `zipfile`, `json`, `threading`) — no DB, no ORM, no async; frontend = pinned exact versions, plain JS (no TypeScript); engine = the `git` CLI itself (formats verified below) instead of a git-binding library. Fewer moving parts → fewer things to break between tasks.

### The engine's single git pass — output format **verified empirically** (probe on git 2.43)

```bash
git -C <git_dir> log --no-merges -M50% --numstat -z --format=%x1f%H%x1f%ct%x1f%an%x1f%ae HEAD
```

Tokens split on NUL (`\0`); `\x1f` separates fields. Verified semantics:

| Stream token | Meaning | Action |
|---|---|---|
| `\x1f<hash>\x1f<ct>\x1f<name>\x1f<email>` | commit header (consecutive headers with no separator = empty commits) | new commit; empty commits count in `\|H\|` |
| `3\t0\ta.txt` | normal entry (may start with one `\n`) | added lines on `a.txt` |
| `0\t2\tsub/b.txt` | deletion | removals recorded on old path (per brief) |
| `-\t-\tbin.dat` | binary file | skipped (per brief) |
| `0\t0\t\0a.txt\0a2.txt` | **rename**: empty 3rd field, next 2 tokens = old, new | counts attributed to **new** path; pure rename 0/0 → no metric change |
| `2\t0\t\0a2.txt\0a3.txt` | rename + edit | only the edit counts, on the new path |
| (dir renames) | emitted as file-level renames: `0\t0\t\0pkg/x.py\0pkg2/x.py` | handled by the same rule |

Commit count for progress: `git rev-list --count --no-merges HEAD`.

## 4. Metric contract (spec → code)

| Brief rule | Implementation |
|---|---|
| H̄ = non-merge commits reachable from HEAD | `--no-merges HEAD` |
| committer date | `%ct` (never author date) |
| `l+`, `l−` per file per commit | numstat counts from git (same definition as brief; binary excluded) |
| `δ = l+ − l−`, `λ = l+ + l−` | per file per commit |
| Directory metrics = recursive sum over immediate children | for every changed path, add its deltas to **all ancestor dirs incl. root** |
| Repository metrics | root directory row |
| `H_t` / `H_i,j` | `ts ≥ t` / `i ≤ ts < j` over committer dates |
| `n` (modifications) = #commits with `λ > 0` on object | count over filtered commit set; includes delete-only commits (λ>0) |
| `η = n/\|H\|`, `ρ = λ/\|H\|` (0 if `\|H\| = 0`) | `\|H\|` includes empty commits |
| Author `n_{H,o,a}`, `λ_{H,o,a}`, `ω = λ_a/λ` (0 if λ=0) | author dimension per object |
| Filters define H | author filter narrows H (and the author table); path filter only selects the displayed object/subtree |
| Author identity after merging | raw `%an/%ae` recorded; mailmap + manual groups resolved in the engine (no re-parse needed) |

## 5. Verification fixture (hand-computed · built by `scripts/make_test_repo.sh`)

| # | Author | Change | numstat |
|---|---|---|---|
| c1 | Alice | add `a.txt` (3 lines), `bin.dat` (binary), `sub/b.txt` (2), `pkg/x.py` (1) | a.txt 3/0; sub/b.txt 2/0; pkg/x.py 1/0; bin.dat skipped |
| c2 | Bob | edit `a.txt`, delete `sub/b.txt` | a.txt 3/1; sub/b.txt 0/2 |
| c3 | Alice | pure rename `a.txt → a2.txt` | a2.txt 0/0 |
| c4 | Bob | rename + edit `a2.txt → a3.txt` | a3.txt 2/0 |
| c5 | Alice | pure dir rename `pkg/ → pkg2/` | pkg2/x.py 0/0 |

**Expected values (H = all 5, `|H|` = 5) — asserted in T5/T7/T8:**

| object | l+ | l− | δ | λ | n | η | ρ |
|---|---|---|---|---|---|---|---|
| **root** | 11 | 3 | 8 | 14 | 3 | 0.6 | 2.8 |
| `sub/` | 2 | 2 | 0 | 4 | 2 | 0.4 | 0.8 |
| `pkg/` | 1 | 0 | 1 | 1 | 1 | 0.2 | 0.2 |
| `pkg2/` | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `a.txt` | 6 | 1 | 5 | 7 | 2 | — | — |
| `a3.txt` | 2 | 0 | 2 | 2 | 1 | — | — |
| `sub/b.txt` | 2 | 2 | 0 | 4 | 2 | — | — |
| `a2.txt`, `pkg2/x.py` | 0 | 0 | 0 | 0 | 0 | — | — |
| `bin.dat` | *excluded (binary)* | | | | | | |

- **Authors (root):** Alice λ=6, n=1, ω≈0.429 · Bob λ=8, n=2, ω≈0.571
- **Author filter = Bob:** `|H|`=2, root l+=5, l−=3, λ=8, n=2, ρ=4.0, η=1.0, ω=1.0
- **Manual list {c1, c4}:** `|H|`=2, root l+=8, l−=0, λ=8, n=2, ρ=4.0

## 6. Repository layout

```
PLAN.md, README.md
server/main.py              Flask app: routes, static serving
server/storage.py           repo registry, zip extract, clone, status
server/engine/parse.py      one-pass git log stream → RepoData facts
server/engine/metrics.py    rollups, commit-set/author aggregations, filters
server/engine/authors.py    mailmap parser + manual merge groups
web/                        Vite + React app (src/, styles.css)
scripts/make_test_repo.sh   T3 fixture builder
scripts/verify_metrics.py   T8 independent verification
data/                       runtime data (gitignored)
```

## 7. API surface (frozen)

```
GET    /api/health
GET    /api/repos                            list (id, name, source, status, commits)
POST   /api/repos                            multipart zip OR json {url} → create + start indexing
DELETE /api/repos/{id}
GET    /api/repos/{id}/status                {status: cloning|indexing|ready|error, progress, error}
GET    /api/repos/{id}/authors               canonical authors + commit counts
GET    /api/repos/{id}/mailmap               parsed .mailmap entries
POST   /api/repos/{id}/merges                {groups:[{canonical, members[]}]}
GET    /api/repos/{id}/commits?q=&limit=     manual-selection list (hash, subject, ts, author)
GET    /api/repos/{id}/tree                  [{path, type}] for the path picker
POST   /api/repos/{id}/metrics               {authorKeys?[], path?,
                                              commits:{mode: all|range|list, start?, end?, hashes?[]}}
  → {|H|, totals, files[], dirs[], authors[], series[]}
```

Dashboard views: **Overview** (stat cards l+/l−/δ/λ/n/η/ρ/|H|, trend line, top-files bar), **Files/Dirs** (sortable tables), **Authors** (ownership pie + table), plus repo management, filter bar, and the author-merge modal.

## 8. Task checklist (≈5 min each, executed in order)

Background jobs (started early, run while we code): `pip install` + `npm install` (T1), cJSON clone (after T2), cJSON indexing (after T4).

- [x] **T1 (5m) P0 — Scaffold & deps.** Project tree, `.gitignore`, Flask skeleton (`/api/health`, static serving), `web/` package.json (react 18, react-dom 18, recharts 2, vite 5 + plugin-react 4, exact pins), minimal React app. Background: `.venv` + `pip install flask`; `npm install`. First git commit.
- [x] **T2 (5m) P0 — Storage + ingestion.** Registry CRUD; zip upload (zip-slip-safe extract, `.git` detection root/1-deep, clear 400 if missing); clone URL (`--mirror`, bg thread, status); `GET/POST /api/repos`, `GET /status`, `DELETE`. Kick off cJSON clone in background. Commit.
- [x] **T3 (5m) P0 — Fixture + expected numbers.** `scripts/make_test_repo.sh` building exactly §5 (both authors), writes `scripts/fixture_expected.json` (per-commit hashes + timestamps + §5 table). Run it. Commit.
- [x] **T4 (5m) P0 — Parser (`engine/parse.py`).** Streaming `git log` parse per §3 rules (header/numstat/rename/binary/empty-commit, path interning), progress counter. Verify on fixture + cJSON. Commit.
- [x] **T5 (7m) P0 — Metrics engine (`engine/metrics.py`).** Per-commit ancestor-dir rollup + root; query layer: commit sets (all/range/manual), author filter, totals, files, dirs, author dims (n_a, λ_a, ω), day/week time series. Fixture assertions from §5 pass. Commit.
- [x] **T6 (5m) P0 — Authors.** Mailmap parse (`git show HEAD:.mailmap`, both forms) + manual merge groups persisted per repo; applied in engine; `authors` / `mailmap` / `merges` endpoints. Verify: cJSON author list; API merge flips results. Commit.
- [x] **T7 (5m) P0 — Metrics API + pickers.** `POST /metrics` per §7; `GET /commits` (search/limit) + `GET /tree`; memo cache keyed by (repo, merge-version, filters). curl checks incl. author/range/list filters. Commit.
- [x] **T8 (5m) P0 — Correctness verification.** `scripts/verify_metrics.py`: (a) fixture API output ≡ §5 exactly; (b) independent ground truth on cJSON — 3 commit ranges compared against `git log --numstat` awk sums, plus rename-only and delete-only commit checks. Fix any discrepancy. Commit. **⏱ Halfway checkpoint — if >40 min elapsed, drop all P2 items below.**
- [x] **T9 (5m) P0 — Frontend shell + repo management.** Layout (sidebar/main), light theme (white/black/blue), API client, toasts/errors, repo list + add (URL input + zip drop), indexing progress poll, Vite dev proxy → :8000. Commit.
- [ ] **T10 (5m) P0 — Filter bar.** Author multi-select, searchable path picker (from `/tree`), commit mode All/Range/Manual (search + checkbox + select-all-shown), debounced refetch applies to all views. Commit.
- [ ] **T11 (8m) P0 — Dashboard views.** Overview cards + trend line + top-files bar chart; Files & Dirs sortable tables; Authors ownership pie + table. **[P2 extras: directory treemap, churn-vs-modifications scatter.]** Numbers must match API on cJSON. Commit.
- [ ] **T12 (4m) P1 — Author merge UI.** Modal: authors with counts, multi-select, canonical name/email picker, POST merges; refresh all views. Verify merge persists per repo E2E. Commit.
- [ ] **T13 (5m) P0 — Integration + smoke + polish.** `npm run build` and serve `dist` via Flask; E2E: zip upload **and** URL clone, all filters, merge; fix issues; README (setup/run/architecture + AI-declaration note); rubric self-audit; RunPreview for you. Commit.

Nominal sum ≈65 min; parallel background jobs save ~5 min. P2 extras (treemap, scatter) ≈5 min are the only droppable scope.

## 9. Rubric traceability

| Rubric requirement (weight) | Covered by |
|---|---|
| All 5 metric categories correct (50%) | T4–T8 |
| Both ingestions: zip + remote URL (50%) | T2, T13 |
| Filtering: repo / author / file-dir / commits (time + manual) (50%) | T7, T10 |
| Author merge: mailmap + manual (50%) | T6, T12 |
| Multi-repo support (50%) | T2, T9 |
| Efficient algorithms & architecture (25%) | T4, T5, T7, T9 (single pass, rollups, cache, bg indexing) |
| Visualisation (25%) | T11 (cards, trend, bars, pie, treemap, scatter) |
| Usability: navigation, error handling, QoL, speed (25%) | T9–T13 |

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| PyPI / npm registry unreachable | **Both verified reachable** (pip dry-run, npm view). Running offline later doesn't affect analysis. |
| Large repos (redis/git ~100k commits) slow to index | Background threading + progress status + RAM cache → queries instant post-index; parse ≈ streaming (O(output)) |
| Memory on huge repos (~1M+ facts) | Path interning + compact tuples; acceptable for target repos; disk cache is an optional stretch |
| Zip without `.git`, broken zips | Explicit validation + clear UI error |
| Time overrun | Priority order + P2 flex + hard cut line at T8 checkpoint |

## 11. Working agreement

1. **Task-gated execution:** I complete ONE task, tick its checkbox, make the small git commit, and hand you a short manual test recipe (command(s) + expected result). I then **stop and wait for your "continue"** before starting the next task.
2. I additionally pause if blocked (dependency/network/env) or at the T8 checkpoint if scope cuts are needed.
3. Nothing outside this plan gets built. The submission repo URL/push is your call (workspace is already `git init`ed).
