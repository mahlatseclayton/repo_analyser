"""Repository registry + ingestion (zip upload and clone URL).

Layout under data/ (gitignored):
    data/repos.json            registry: id -> metadata
    data/repos/<id>/work/      extracted zip repo (git_dir points inside)
    data/repos/<id>/repo.git/  mirror clone (git_dir points here)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
REPOS_DIR = DATA_DIR / "repos"
REGISTRY_FILE = DATA_DIR / "repos.json"

_lock = threading.RLock()
_registry: dict[str, dict] | None = None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _load() -> dict[str, dict]:
    global _registry
    if _registry is None:
        if REGISTRY_FILE.exists():
            _registry = json.loads(REGISTRY_FILE.read_text())
        else:
            _registry = {}
    return _registry


def _save() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(_registry, indent=2))


def _repo_dir(rid: str) -> Path:
    return REPOS_DIR / rid


def _rel(path: Path) -> str:
    return str(path.relative_to(DATA_DIR))


def git_dir_for(meta: dict) -> Path | None:
    """Absolute path of the git directory for a registry entry."""
    gd = meta.get("git_dir")
    return (DATA_DIR / gd) if gd else None


def list_repos() -> list[dict]:
    with _lock:
        reg = _load()
        return [
            dict(m)
            for m in sorted(reg.values(), key=lambda m: m.get("created_at", ""), reverse=True)
        ]


def get_repo(rid: str) -> dict | None:
    with _lock:
        meta = _load().get(rid)
        return dict(meta) if meta else None


def delete_repo(rid: str) -> bool:
    with _lock:
        reg = _load()
        if rid not in reg:
            return False
        del reg[rid]
        _save()
    repo_dir = _repo_dir(rid)
    # Safety: only remove directories directly under REPOS_DIR.
    if repo_dir.resolve().parent == REPOS_DIR.resolve():
        shutil.rmtree(repo_dir, ignore_errors=True)
    return True


def update_repo(rid: str, **fields) -> None:
    with _lock:
        reg = _load()
        if rid in reg:
            reg[rid].update(fields)
            _save()


def _new_id(name: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-") or "repo"
    return f"{slug[:40]}-{uuid.uuid4().hex[:6]}"


def _new_meta(rid: str, name: str, source: dict, status: str) -> dict:
    return {
        "id": rid,
        "name": name,
        "source": source,
        "status": status,
        "error": None,
        "git_dir": None,
        "commit_count": None,
        "progress": None,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Zip ingestion
# ---------------------------------------------------------------------------

def register_zip(file_storage) -> dict:
    """Ingest an uploaded zip that contains the repo including its .git."""
    raw_name = file_storage.filename or "upload.zip"
    name = Path(raw_name).stem or "repo"

    with _lock:
        rid = _new_id(name)
        _load()[rid] = _new_meta(rid, name, {"type": "zip", "filename": raw_name}, "extracting")
        _save()

    try:
        repo_dir = _repo_dir(rid)
        repo_dir.mkdir(parents=True, exist_ok=True)
        zip_path = repo_dir / "upload.zip"
        file_storage.save(zip_path)

        work = repo_dir / "work"
        work.mkdir()
        with zipfile.ZipFile(zip_path) as zf:
            _safe_extract(zf, work)

        git_root = _find_git_root(work)
        if git_root is None:
            raise ValueError(
                "No .git directory found in the zip (checked the root and one level down). "
                "Upload a zip of the repository that includes its .git directory."
            )
        zip_path.unlink(missing_ok=True)
        update_repo(rid, status="ready", git_dir=_rel(git_root))
    except zipfile.BadZipFile as e:
        update_repo(rid, status="error", error="Uploaded file is not a valid zip archive.")
        raise ValueError("Uploaded file is not a valid zip archive.") from e
    except Exception as e:  # noqa: BLE001
        update_repo(rid, status="error", error=str(e))
        raise
    meta = get_repo(rid)
    assert meta is not None
    return meta


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Reject absolute paths and '..' traversal before extracting."""
    for info in zf.infolist():
        p = Path(info.filename)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"Unsafe path in zip: {info.filename}")
    zf.extractall(dest)


def _find_git_root(root: Path) -> Path | None:
    """Locate the working tree containing .git (root or one level down)."""
    if (root / ".git").is_dir():
        return root
    if (root / ".git").is_file():  # gitdir pointer (worktree); accept if it resolves inside
        line = (root / ".git").read_text(errors="replace").strip()
        if line.startswith("gitdir:"):
            target = (root / line.split(":", 1)[1].strip()).resolve()
            if target.is_dir() and str(target).startswith(str(root.resolve())):
                return root
    for child in sorted(root.iterdir()):
        if child.is_dir() and not child.name.startswith(".") and (child / ".git").is_dir():
            return child
    return None


# ---------------------------------------------------------------------------
# Clone ingestion
# ---------------------------------------------------------------------------

_ALLOWED_PREFIXES = ("https://", "http://", "ssh://", "git@")


def register_clone(url: str) -> dict:
    """Start a deep (mirror) clone in the background; poll via status."""
    url = url.strip()
    if not url.startswith(_ALLOWED_PREFIXES):
        raise ValueError("URL must start with https://, http://, ssh:// or git@")
    name = url.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    name = name or "repo"

    with _lock:
        rid = _new_id(name)
        _load()[rid] = _new_meta(rid, name, {"type": "url", "url": url}, "cloning")
        _save()
    threading.Thread(target=_clone_worker, args=(rid, url), daemon=True).start()
    meta = get_repo(rid)
    assert meta is not None
    return meta


def _clone_worker(rid: str, url: str) -> None:
    dest = _repo_dir(rid) / "repo.git"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["git", "clone", "--mirror", url, str(dest)],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if proc.returncode != 0:
            tail = [line for line in (proc.stderr or "").strip().splitlines() if line][-3:]
            raise RuntimeError("git clone failed: " + " | ".join(tail))
        update_repo(rid, status="ready", git_dir=_rel(dest))
    except Exception as e:  # noqa: BLE001
        update_repo(rid, status="error", error=str(e))
