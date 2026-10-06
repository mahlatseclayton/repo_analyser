"""Streaming parser: one `git log` pass per repository -> RepoData facts.

Output format verified empirically on git 2.43 (PLAN.md §3):

    git -C <git_dir> log --no-merges -M50% --numstat -z --format=<...> HEAD

Records are NUL-separated. A record starting with \x1f is a commit header
(fields: hash, committer ts, author name, author email, subject). Other
records are numstat entries:

    "<added>\\t<removed>\\t<path>"          normal change (deletion keeps old path)
    "-\\t-\\t<path>"                        binary file -> skipped (not measured)
    "<added>\\t<removed>\\t" + 2 records    rename: next records are old, then
                                            new path; counts go to the NEW path

Commits with no entries (empty commits) are still recorded and count in |H|.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

FIELD_SEP = b"\x1f"
RECORD_SEP = b"\x00"
_LOG_FORMAT = "%x1f%H%x1f%ct%x1f%an%x1f%ae%x1f%s"
_CHUNK = 1 << 16


@dataclass
class Commit:
    hash: str
    author_id: int
    ts: int
    subject: str = ""
    facts: list = field(default_factory=list)  # [(path_id, added, removed), ...]


class RepoData:
    """Interred path/author tables plus the commit fact list."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self._path_ids: dict[str, int] = {}
        self.authors: list[tuple[str, str]] = []
        self._author_ids: dict[tuple[str, str], int] = {}
        self.commits: list[Commit] = []

    def intern_path(self, path: str) -> int:
        pid = self._path_ids.get(path)
        if pid is None:
            pid = len(self.paths)
            self.paths.append(path)
            self._path_ids[path] = pid
        return pid

    def intern_author(self, name: str, email: str) -> int:
        key = (name, email)
        aid = self._author_ids.get(key)
        if aid is None:
            aid = len(self.authors)
            self.authors.append(key)
            self._author_ids[key] = aid
        return aid


def count_commits(git_dir) -> int | None:
    """Total non-merge commits reachable from HEAD (for progress reporting)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(git_dir), "rev-list", "--count", "--no-merges", "HEAD"],
            capture_output=True, text=True, timeout=300,
        )
        return int(proc.stdout.strip()) if proc.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def parse_repo(git_dir, progress=None) -> RepoData:
    """Parse every non-merge commit reachable from HEAD.

    progress: optional callable(parsed, total_or_None), called periodically.
    """
    git_dir = str(git_dir)
    cmd = [
        "git", "-C", git_dir, "log", "--no-merges", "-M50%",
        "--numstat", "-z", f"--format={_LOG_FORMAT}", "HEAD",
    ]
    data = RepoData()
    total = count_commits(git_dir)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None and proc.stderr is not None

    current: Commit | None = None
    pending_rename = 0          # 2 = need old path, 1 = need new path
    rename_counts = (0, 0)

    def handle(token: bytes) -> None:
        nonlocal current, pending_rename, rename_counts

        if pending_rename:
            if pending_rename == 2:
                pending_rename = 1  # old path consumed (unused; counts go to new)
            else:
                path = token.decode("utf-8", "replace")
                if current is not None:
                    a, r = rename_counts
                    current.facts.append((data.intern_path(path), a, r))
                pending_rename = 0
            return

        if token.startswith(FIELD_SEP):
            parts = token.split(FIELD_SEP, 5)
            if len(parts) < 5:
                return  # malformed header, skip
            current = Commit(
                hash=parts[1].decode("utf-8", "replace"),
                ts=int(parts[2]),
                author_id=data.intern_author(
                    parts[3].decode("utf-8", "replace"),
                    parts[4].decode("utf-8", "replace"),
                ),
                subject=(parts[5].decode("utf-8", "replace") if len(parts) > 5 else ""),
            )
            data.commits.append(current)
            if progress and len(data.commits) % 500 == 0:
                progress(len(data.commits), total)
            return

        text = token[1:] if token[:1] == b"\n" else token  # first entry after header
        if not text:
            return
        parts = text.split(b"\t", 2)
        if len(parts) != 3:
            return
        added_b, removed_b, path_b = parts
        if added_b == b"-" or removed_b == b"-":
            return  # binary file: not measured
        if current is None:
            return  # stray entry before any header
        added = int(added_b)
        removed = int(removed_b)
        if path_b == b"":
            rename_counts = (added, removed)
            pending_rename = 2
            return
        current.facts.append(
            (data.intern_path(path_b.decode("utf-8", "replace")), added, removed)
        )

    buf = b""
    while True:
        chunk = proc.stdout.read(_CHUNK)
        if not chunk:
            break
        buf += chunk
        *tokens, buf = buf.split(RECORD_SEP)
        for token in tokens:
            if token:
                handle(token)
    if buf:
        handle(buf)

    rc = proc.wait()
    if rc != 0:
        err = proc.stderr.read().decode("utf-8", "replace").strip()
        raise RuntimeError(f"git log failed ({rc}): {err[-500:] if err else 'unknown error'}")
    if progress:
        progress(len(data.commits), total)
    return data


def git_dir_or_raise(git_dir) -> Path:
    path = Path(git_dir)
    if not path.exists():
        raise FileNotFoundError(f"git directory not found: {path}")
    return path
