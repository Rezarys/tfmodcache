"""The mirror on disk: one extracted copy per (source, version), plus an index.

The store holds whole module trees, keyed by an identity that already contains the
version or the git ref, so an entry is never invalidated in place. Nothing here
talks to a network or to a shared remote: the cache is local, and stays local.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from typing import Dict, List, NamedTuple, Optional

INDEX_NAME = "index.json"
MODULES_DIR = "modules"
_INDEX_VERSION = 1


class Entry(NamedTuple):
    """One cached module tree."""

    key: str
    digest: str
    path: str
    source: str
    version: Optional[str]
    resolved: str
    fetched_at: float
    bytes: int
    files: int

    def as_json(self) -> Dict:
        data = self._asdict()
        data.pop("path")
        return data


def default_root() -> str:
    """Where the cache lives, honouring the usual environment overrides."""
    explicit = os.environ.get("TFMODCACHE_HOME")
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return os.path.join(os.path.abspath(os.path.expanduser(base)), "tfmodcache")
    return os.path.join(os.path.expanduser("~"), ".cache", "tfmodcache")


def digest_for(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def measure(path: str) -> tuple:
    """Return the byte size and file count of a directory tree."""
    total = 0
    count = 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            full = os.path.join(root, name)
            if os.path.islink(full):
                continue
            try:
                total += os.path.getsize(full)
            except OSError:
                continue
            count += 1
    return total, count


class Store:
    """A directory of cached module trees with a JSON index beside them."""

    def __init__(self, root: Optional[str] = None):
        self.root = os.path.abspath(root) if root else default_root()
        self.modules_root = os.path.join(self.root, MODULES_DIR)
        self.index_path = os.path.join(self.root, INDEX_NAME)

    # -- index ------------------------------------------------------------

    def load(self) -> Dict[str, Dict]:
        try:
            with open(self.index_path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError):
            return {}
        if document.get("version") != _INDEX_VERSION:
            return {}
        entries = document.get("entries")
        return entries if isinstance(entries, dict) else {}

    def save(self, entries: Dict[str, Dict]) -> None:
        os.makedirs(self.root, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.root, prefix=".index-", suffix=".tmp", delete=False
        )
        try:
            json.dump({"version": _INDEX_VERSION, "entries": entries}, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.close()
            os.replace(handle.name, self.index_path)
        except BaseException:
            handle.close()
            _quiet_remove(handle.name)
            raise

    # -- entries ----------------------------------------------------------

    def path_for(self, key: str) -> str:
        return os.path.join(self.modules_root, digest_for(key))

    def get(self, key: str) -> Optional[Entry]:
        record = self.load().get(key)
        if not record:
            return None
        path = self.path_for(key)
        if not os.path.isdir(path):
            return None
        return Entry(
            key=key,
            digest=record.get("digest", digest_for(key)),
            path=path,
            source=record.get("source", ""),
            version=record.get("version"),
            resolved=record.get("resolved", ""),
            fetched_at=float(record.get("fetched_at", 0.0)),
            bytes=int(record.get("bytes", 0)),
            files=int(record.get("files", 0)),
        )

    def entries(self) -> List[Entry]:
        found = []
        for key in sorted(self.load()):
            entry = self.get(key)
            if entry is not None:
                found.append(entry)
        return found

    def stage(self) -> str:
        """A scratch directory on the same filesystem as the store."""
        os.makedirs(self.modules_root, exist_ok=True)
        return tempfile.mkdtemp(prefix=".staging-", dir=self.modules_root)

    def commit(self, key: str, staged: str, source: str, version: Optional[str], resolved: str) -> Entry:
        """Move a staged tree into place and record it. Replaces any previous copy."""
        destination = self.path_for(key)
        os.makedirs(self.modules_root, exist_ok=True)
        if os.path.isdir(destination):
            shutil.rmtree(destination, ignore_errors=True)
        os.replace(staged, destination)
        size, files = measure(destination)
        entry = Entry(
            key=key,
            digest=digest_for(key),
            path=destination,
            source=source,
            version=version,
            resolved=resolved,
            fetched_at=time.time(),
            bytes=size,
            files=files,
        )
        entries = self.load()
        entries[key] = entry.as_json()
        self.save(entries)
        return entry

    def forget(self, keys: List[str]) -> int:
        entries = self.load()
        removed = 0
        for key in keys:
            path = self.path_for(key)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            if entries.pop(key, None) is not None:
                removed += 1
        self.save(entries)
        return removed

    def clear(self) -> int:
        count = len(self.load())
        if os.path.isdir(self.modules_root):
            shutil.rmtree(self.modules_root, ignore_errors=True)
        _quiet_remove(self.index_path)
        return count

    def prune_orphans(self) -> int:
        """Drop directories no index entry points at, and entries with no directory."""
        entries = self.load()
        wanted = {record.get("digest", digest_for(key)) for key, record in entries.items()}
        removed = 0
        if os.path.isdir(self.modules_root):
            for name in os.listdir(self.modules_root):
                if name in wanted:
                    continue
                shutil.rmtree(os.path.join(self.modules_root, name), ignore_errors=True)
                removed += 1
        stale = [key for key in entries if not os.path.isdir(self.path_for(key))]
        for key in stale:
            entries.pop(key, None)
            removed += 1
        if stale:
            self.save(entries)
        return removed

    def total_bytes(self) -> int:
        return sum(entry.bytes for entry in self.entries())


def _quiet_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
