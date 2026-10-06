"""Author identity resolution: .mailmap rewrite + manual merge groups (T6).

Pipeline for a raw commit identity (name, email):
  1. .mailmap (git's four forms) rewrites the name/email
  2. manual merge groups fold the rewritten key into a canonical key
  3. otherwise the key stays "Name <email>"

Keys are the strings the UI shows. Merge-group members and canonical values
are keys of step-2 inputs (i.e. post-mailmap, pre-merge = "base keys").
"""
from __future__ import annotations

import re
import subprocess

_EMAIL_RE = re.compile(r"<([^<>]+)>")
_KEY_RE = re.compile(r"^\s*(.*?)\s*<([^<>]*)>\s*$")


def format_key(name: str, email: str) -> str:
    return f"{name} <{email}>"


def split_key(key: str) -> tuple[str, str]:
    """'Name <email>' -> (name, email); unparseable keys return (key, '')."""
    m = _KEY_RE.match(key or "")
    if not m:
        return (key or "").strip(), ""
    return m.group(1), m.group(2)


def parse_mailmap(text: str) -> list[dict]:
    """Parse a .mailmap body into entries (git's four forms).

    Form 1: Proper Name <proper@email> Commit Name <commit@email>
    Form 2: Proper Name <proper@email> <commit@email>
    Form 3: <proper@email> <commit@email>
    Form 4: Proper Name <proper@email>          (rename own commits)
    """
    entries = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        emails = _EMAIL_RE.findall(line)
        if not emails:
            continue
        before = line.split("<", 1)[0].strip()
        if len(emails) >= 2:
            between = line.split(">", 1)[1].rsplit("<", 1)[0].strip()
            entries.append({
                "commit_name": between or None,
                "commit_email": emails[-1],
                "proper_name": before or None,
                "proper_email": emails[0],
            })
        else:
            entries.append({
                "commit_name": None,
                "commit_email": emails[0],
                "proper_name": before or None,
                "proper_email": None,
            })
    return entries


def load_mailmap(git_dir) -> list[dict]:
    """Parsed .mailmap from HEAD of a repo ([] when absent)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(git_dir), "show", "HEAD:.mailmap"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:  # noqa: BLE001
        return []
    if proc.returncode != 0:
        return []
    return parse_mailmap(proc.stdout)


class AuthorResolver:
    """Callable (name, email, aid) -> (key, name, email); engine-compatible."""

    def __init__(self, mailmap_entries=None, merge_groups=None):
        self.entries = list(mailmap_entries or [])
        self._by_pair: dict[tuple[str, str], tuple] = {}
        self._by_email: dict[str, tuple] = {}
        for e in self.entries:
            tgt = (e.get("proper_name"), e.get("proper_email"))
            if e.get("commit_name"):
                # Named entry: only matches commits with that exact name.
                self._by_pair[(e["commit_email"], e["commit_name"])] = tgt
            else:
                # Name-less entry: applies to any commit with that email.
                self._by_email.setdefault(e["commit_email"], tgt)

        self.groups: list[dict] = []
        self._member_to_key: dict[str, str] = {}
        for g in (merge_groups or []):
            canonical = str(g.get("canonical") or "").strip()
            members = [str(m).strip() for m in (g.get("members") or []) if str(m).strip()]
            if not canonical or not members:
                continue
            for m in members:
                self._member_to_key[m] = canonical
            self.groups.append({"canonical": canonical, "members": members})

    # -- identity pipeline --------------------------------------------------

    def rewrite(self, name: str, email: str) -> tuple[str, str]:
        """Apply .mailmap only."""
        tgt = self._by_pair.get((email, name)) or self._by_email.get(email)
        if not tgt:
            return name, email
        pn, pe = tgt
        return (pn or name), (pe or email)

    def base_key(self, name: str, email: str) -> str:
        """Post-mailmap, pre-merge key (valid merge-group member form)."""
        n, e = self.rewrite(name, email)
        return format_key(n, e)

    def __call__(self, name: str, email: str, _aid: int = 0) -> tuple[str, str, str]:
        base = self.base_key(name, email)
        key = self._member_to_key.get(base, base)
        kn, ke = split_key(key)
        return key, kn, ke
