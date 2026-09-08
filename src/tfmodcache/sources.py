"""Classify a module ``source`` string.

Terraform accepts many source kinds. This tool caches the two that dominate real
configurations, registry modules and git modules, and hands everything else back
to Terraform untouched. A source it does not understand is never guessed at.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

LOCAL = "local"
REGISTRY = "registry"
GIT = "git"
UNSUPPORTED = "unsupported"

DEFAULT_REGISTRY_HOST = "registry.terraform.io"

_REGISTRY_PART = r"[0-9A-Za-z](?:[0-9A-Za-z-_]*[0-9A-Za-z])?"
_REGISTRY = re.compile(
    r"^(?:(?P<host>[0-9A-Za-z.-]+\.[0-9A-Za-z-]+(?::\d+)?)/)?"
    r"(?P<namespace>" + _REGISTRY_PART + r")/"
    r"(?P<name>" + _REGISTRY_PART + r")/"
    r"(?P<provider>" + _REGISTRY_PART + r")$"
)
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*::")


class Source(NamedTuple):
    """A module source, split into what the fetchers need."""

    kind: str
    raw: str
    subdir: str = ""
    host: str = ""
    namespace: str = ""
    name: str = ""
    provider: str = ""
    url: str = ""
    ref: str = ""
    reason: str = ""

    @property
    def registry_path(self) -> str:
        return "{}/{}/{}/{}".format(self.host, self.namespace, self.name, self.provider)


def split_subdir(source: str) -> tuple:
    """Split the go-getter ``//subdir`` suffix off a source string."""
    scheme_end = 0
    match = _SCHEME.match(source)
    if match:
        scheme_end = match.end()
    protocol = source.find("://", scheme_end)
    search_from = protocol + 3 if protocol != -1 else scheme_end
    index = source.find("//", search_from)
    if index == -1:
        return source, ""
    return source[:index], source[index + 2 :].strip("/")


def classify(source: str) -> Source:
    """Return how *source* should be fetched, without touching the network."""
    raw = source.strip()
    if not raw:
        return Source(UNSUPPORTED, source, reason="empty source")
    if raw.startswith("./") or raw.startswith("../") or raw in (".", ".."):
        return Source(LOCAL, raw)

    base, subdir = split_subdir(raw)

    if base.startswith("git::"):
        url, ref = split_ref(base[len("git::") :])
        return Source(GIT, raw, subdir=subdir, url=url, ref=ref)
    if base.startswith("git@") or base.endswith(".git"):
        url, ref = split_ref(base)
        return Source(GIT, raw, subdir=subdir, url=url, ref=ref)
    for prefix in ("github.com/", "bitbucket.org/"):
        if base.startswith(prefix):
            url, ref = split_ref("https://" + base)
            if not url.endswith(".git"):
                url += ".git"
            return Source(GIT, raw, subdir=subdir, url=url, ref=ref)

    if _SCHEME.match(base) or "://" in base:
        return Source(UNSUPPORTED, raw, reason="unsupported source scheme")

    match = _REGISTRY.match(base)
    if match:
        return Source(
            REGISTRY,
            raw,
            subdir=subdir,
            host=match.group("host") or DEFAULT_REGISTRY_HOST,
            namespace=match.group("namespace"),
            name=match.group("name"),
            provider=match.group("provider"),
        )
    return Source(UNSUPPORTED, raw, reason="source is not a registry or git address")


def split_ref(url: str) -> tuple:
    """Pull the ``?ref=`` query out of a git URL, keeping any other query intact."""
    if "?" not in url:
        return url, ""
    head, query = url.split("?", 1)
    kept = []
    ref = ""
    for item in query.split("&"):
        if item.startswith("ref="):
            ref = item[len("ref=") :]
        elif item:
            kept.append(item)
    if kept:
        head = head + "?" + "&".join(kept)
    return head, ref


def cache_key(source: Source, version: Optional[str] = None) -> str:
    """A stable identity for one cached module, before any subdirectory is applied."""
    if source.kind == REGISTRY:
        return "registry/{}/{}".format(source.registry_path, version or "latest")
    if source.kind == GIT:
        return "git/{}/{}".format(source.url, source.ref or "HEAD")
    raise ValueError("only registry and git sources are cached")
