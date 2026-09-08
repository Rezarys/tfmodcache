"""Walk a configuration, fill the cache, and lay the modules out where Terraform looks.

Terraform reads modules from ``.terraform/modules`` and trusts the manifest
``.terraform/modules/modules.json``. This module builds both from the local mirror,
so a later ``terraform init`` finds every module already on disk.

If anything here is wrong or missing, Terraform simply downloads the module itself:
the worst case is the behaviour you had before installing this tool.
"""

from __future__ import annotations

import os
import shutil
from typing import Dict, List, NamedTuple, Optional

from . import fetch, hcl, sources
from .errors import FetchError
from .registry import Registry, pick_version
from .store import Store

MODULES_SUBPATH = os.path.join(".terraform", "modules")
MANIFEST_NAME = "modules.json"
MAX_DEPTH = 32


class Note(NamedTuple):
    """One module this run did not cache, and why. Terraform will handle it."""

    key: str
    source: str
    reason: str


class Record(NamedTuple):
    """One line of the Terraform module manifest."""

    key: str
    source: str
    version: Optional[str]
    dir: str

    def as_manifest(self) -> Dict:
        item = {"Key": self.key, "Source": self.source, "Dir": self.dir}
        if self.version:
            item["Version"] = self.version
        return item


class Outcome(NamedTuple):
    records: List[Record]
    fetched: List[str]
    reused: List[str]
    skipped: List[Note]

    @property
    def cached_count(self) -> int:
        return len(self.fetched) + len(self.reused)


class Resolver:
    """Resolve one project directory against one store."""

    def __init__(
        self,
        store: Store,
        offline: bool = False,
        timeout: float = 30.0,
        use_symlinks: bool = False,
    ):
        self.store = store
        self.offline = offline
        self.timeout = timeout
        self.use_symlinks = use_symlinks
        self._registries: Dict[str, Registry] = {}

    # -- public -----------------------------------------------------------

    def warm(self, project: str) -> Outcome:
        """Fill the cache for *project* without writing anything into it."""
        return self._walk(project, materialise=False)

    def link(self, project: str) -> Outcome:
        """Fill the cache and lay ``.terraform/modules`` out from it."""
        outcome = self._walk(project, materialise=True)
        self._write_manifest(project, outcome.records)
        return outcome

    # -- the walk ---------------------------------------------------------

    def _walk(self, project: str, materialise: bool) -> Outcome:
        project = os.path.abspath(project)
        records: List[Record] = [Record("", "", None, ".")]
        fetched: List[str] = []
        reused: List[str] = []
        skipped: List[Note] = []
        seen: set = set()

        # Each item: manifest key prefix, directory to read, directory as Terraform names it.
        pending = [("", project, ".", 0)]
        while pending:
            prefix, read_dir, manifest_dir, depth = pending.pop(0)
            if depth > MAX_DEPTH:
                continue
            for call in hcl.read_module_calls(read_dir):
                key = call.name if not prefix else "{}.{}".format(prefix, call.name)
                if key in seen:
                    continue
                seen.add(key)
                source = sources.classify(call.source)

                if source.kind == sources.LOCAL:
                    child_manifest = _join_relative(manifest_dir, call.source)
                    records.append(Record(key, call.source, None, child_manifest))
                    child_read = os.path.normpath(os.path.join(read_dir, call.source))
                    pending.append((key, child_read, child_manifest, depth + 1))
                    continue

                if source.kind == sources.UNSUPPORTED:
                    skipped.append(Note(key, call.source, source.reason))
                    continue

                try:
                    entry, version, was_fetched = self._ensure(source, call.version)
                except FetchError as error:
                    skipped.append(Note(key, call.source, str(error)))
                    continue

                (fetched if was_fetched else reused).append(key)
                target_manifest = os.path.join(MODULES_SUBPATH, key)
                record_dir = (
                    os.path.join(target_manifest, source.subdir) if source.subdir else target_manifest
                )
                records.append(Record(key, call.source, version, record_dir))

                if not materialise:
                    child_read = os.path.join(entry.path, source.subdir) if source.subdir else entry.path
                    pending.append((key, child_read, record_dir, depth + 1))
                    continue

                destination = os.path.join(project, target_manifest)
                _place(entry.path, destination, self.use_symlinks)
                pending.append((key, os.path.join(project, record_dir), record_dir, depth + 1))

        return Outcome(records, fetched, reused, skipped)

    # -- one module -------------------------------------------------------

    def _ensure(self, source: sources.Source, declared_version: Optional[str]):
        """Return the cached entry for *source*, downloading it only if missing."""
        if source.kind == sources.REGISTRY:
            version = self._registry_version(source, declared_version)
            key = sources.cache_key(source, version)
        else:
            version = None
            key = sources.cache_key(source)

        existing = self.store.get(key)
        if existing is not None:
            return existing, version, False
        if self.offline:
            raise FetchError("not in the cache and offline mode is on")

        staged = self.store.stage()
        try:
            if source.kind == sources.REGISTRY:
                registry = self._registry(source.host)
                address = registry.download_address(
                    source.namespace, source.name, source.provider, version
                )
                resolved = self._download(address, staged)
            else:
                resolved = fetch.fetch_git(source.url, source.ref, staged)
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
        entry = self.store.commit(key, staged, source.raw, version, resolved)
        return entry, version, True

    def _download(self, address: str, staged: str) -> str:
        """Follow one go-getter address into a staging directory."""
        base, subdir = sources.split_subdir(address)
        if base.startswith("git::"):
            base = base[len("git::") :]
            url, ref = sources.split_ref(base)
            commit = fetch.fetch_git(url, ref, staged)
            _pull_subdir_up(staged, subdir)
            return "git::{}?ref={}".format(url, commit) if commit else url
        if fetch.is_archive(base) or base.startswith(("http://", "https://")):
            fetch.fetch_archive(base, staged, timeout=self.timeout)
            _pull_subdir_up(staged, subdir)
            return base
        raise FetchError("unsupported download address {}".format(address))

    def _registry_version(self, source: sources.Source, declared: Optional[str]) -> str:
        wanted = (declared or "").strip()
        if wanted and _is_exact(wanted):
            return wanted.lstrip("=v ").strip()
        if self.offline:
            raise FetchError("version {!r} is not exact and offline mode is on".format(declared))
        registry = self._registry(source.host)
        return pick_version(registry.versions(source.namespace, source.name, source.provider), declared)

    def _registry(self, host: str) -> Registry:
        if host not in self._registries:
            self._registries[host] = Registry(host, timeout=self.timeout, scheme=_scheme_for(host))
        return self._registries[host]

    # -- manifest ---------------------------------------------------------

    def _write_manifest(self, project: str, records: List[Record]) -> str:
        import json
        import tempfile

        directory = os.path.join(project, MODULES_SUBPATH)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, MANIFEST_NAME)
        payload = {"Modules": [record.as_manifest() for record in records]}
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory, prefix=".modules-", suffix=".tmp", delete=False
        )
        try:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.close()
            os.replace(handle.name, path)
        except BaseException:
            handle.close()
            try:
                os.remove(handle.name)
            except OSError:
                pass
            raise
        return path


# -- helpers --------------------------------------------------------------


HTTP_REGISTRIES_ENV = "TFMODCACHE_HTTP_REGISTRIES"


def _scheme_for(host: str) -> str:
    """Registries are HTTPS, unless a host is named in the plain HTTP list."""
    allowed = os.environ.get(HTTP_REGISTRIES_ENV, "")
    names = {item.strip() for item in allowed.split(",") if item.strip()}
    return "http" if host in names else "https"


_RANGE_TOKENS = ("~", ">", "<", ",", "!", "*", " ")


def _is_exact(constraint: str) -> bool:
    """True when the constraint names one published version and nothing else."""
    body = constraint.strip().lstrip("=v ").strip()
    if not body or any(token in body for token in _RANGE_TOKENS):
        return False
    return all(character.isalnum() or character in "-+." for character in body)


def _join_relative(parent: str, relative: str) -> str:
    joined = os.path.normpath(os.path.join(parent, relative))
    return joined.replace(os.sep, "/")


def _place(origin: str, destination: str, use_symlinks: bool) -> None:
    parent = os.path.dirname(destination)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.islink(destination):
        os.unlink(destination)
    elif os.path.isdir(destination):
        shutil.rmtree(destination, ignore_errors=True)
    if use_symlinks:
        os.symlink(origin, destination)
        return
    shutil.copytree(origin, destination, symlinks=True)


def _pull_subdir_up(staged: str, subdir: str) -> None:
    """When the download address names a subdirectory, keep only that subtree."""
    if not subdir:
        return
    inner = os.path.normpath(os.path.join(staged, subdir))
    if not os.path.isdir(inner) or os.path.normpath(staged) == inner:
        return
    holding = staged + ".subdir"
    shutil.move(inner, holding)
    for name in os.listdir(staged):
        full = os.path.join(staged, name)
        shutil.rmtree(full, ignore_errors=True) if os.path.isdir(full) else os.remove(full)
    for name in os.listdir(holding):
        shutil.move(os.path.join(holding, name), os.path.join(staged, name))
    shutil.rmtree(holding, ignore_errors=True)
