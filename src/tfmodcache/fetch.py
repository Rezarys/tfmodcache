"""Getting a module tree onto disk, once.

Two shapes are handled, and they are the two the registry protocol produces:
a git address with an optional ref, and a plain archive over HTTPS. Everything is
written to a staging directory first, so a failed download never leaves a partial
module in the cache.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import urllib.error
import urllib.request
import zipfile
from typing import Optional

from . import __version__
from .errors import FetchError

USER_AGENT = "tfmodcache/{}".format(__version__)
GIT_TIMEOUT = 300
ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".zip")
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


def git_available() -> bool:
    return shutil.which("git") is not None


def fetch_git(url: str, ref: str, destination: str) -> str:
    """Clone one revision of *url* into *destination*. Returns the commit id."""
    if not git_available():
        raise FetchError("git is not on PATH, so git module sources cannot be cached")
    os.makedirs(destination, exist_ok=True)
    _git(["init", "--quiet"], destination)
    _git(["remote", "add", "origin", url], destination)
    target = ref or "HEAD"
    try:
        _git(["fetch", "--quiet", "--depth", "1", "origin", target], destination)
        _git(["checkout", "--quiet", "FETCH_HEAD"], destination)
    except FetchError:
        # A shallow fetch of a bare commit id is refused by some servers.
        _git(["fetch", "--quiet", "origin"], destination)
        _git(["checkout", "--quiet", target], destination)
    commit = _git(["rev-parse", "HEAD"], destination).strip()
    shutil.rmtree(os.path.join(destination, ".git"), ignore_errors=True)
    return commit


def fetch_archive(url: str, destination: str, timeout: float = 120.0) -> str:
    """Download and unpack a tar or zip archive into *destination*."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(_MAX_ARCHIVE_BYTES + 1)
    except (urllib.error.URLError, OSError) as error:
        raise FetchError("could not download {}: {}".format(url, error)) from error
    if len(payload) > _MAX_ARCHIVE_BYTES:
        raise FetchError("archive at {} is larger than the 512 MiB limit".format(url))
    os.makedirs(destination, exist_ok=True)
    if url.split("?", 1)[0].endswith(".zip"):
        _extract_zip(payload, destination)
    else:
        _extract_tar(payload, destination)
    _lift_single_root(destination)
    return url


def is_archive(url: str) -> bool:
    return url.split("?", 1)[0].endswith(ARCHIVE_SUFFIXES)


# -- plumbing -------------------------------------------------------------


def _git(arguments, cwd: str) -> str:
    command = ["git"] + list(arguments)
    environment = dict(os.environ)
    environment.setdefault("GIT_TERMINAL_PROMPT", "0")
    environment.setdefault("GIT_ASKPASS", "true")
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FetchError("git {} failed: {}".format(" ".join(arguments), error)) from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise FetchError(
            "git {} failed: {}".format(" ".join(arguments), detail[-1] if detail else "no output")
        )
    return completed.stdout.decode("utf-8", "replace")


def _safe_member_path(name: str, destination: str) -> Optional[str]:
    target = os.path.normpath(os.path.join(destination, name))
    root = os.path.normpath(destination)
    if target == root or target.startswith(root + os.sep):
        return target
    return None


def _extract_tar(payload: bytes, destination: str) -> None:
    try:
        archive = tarfile.open(fileobj=io.BytesIO(payload), mode="r:*")
    except tarfile.TarError as error:
        raise FetchError("archive is not a readable tar file: {}".format(error)) from error
    with archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                continue
            if _safe_member_path(member.name, destination) is None:
                raise FetchError("archive contains a path outside its own directory")
        archive.extractall(destination)


def _extract_zip(payload: bytes, destination: str) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        raise FetchError("archive is not a readable zip file: {}".format(error)) from error
    with archive:
        for name in archive.namelist():
            if _safe_member_path(name, destination) is None:
                raise FetchError("archive contains a path outside its own directory")
        archive.extractall(destination)


def _lift_single_root(destination: str) -> None:
    """Archives from code hosts wrap everything in one directory; unwrap it."""
    names = [name for name in os.listdir(destination) if not name.startswith(".")]
    if len(names) != 1:
        return
    inner = os.path.join(destination, names[0])
    if not os.path.isdir(inner):
        return
    for name in os.listdir(inner):
        shutil.move(os.path.join(inner, name), os.path.join(destination, name))
    shutil.rmtree(inner, ignore_errors=True)
