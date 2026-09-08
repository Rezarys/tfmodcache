"""The command line."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from typing import List, Optional

from . import __version__
from .errors import TfmodcacheError
from .resolve import Outcome, Resolver
from .store import Store

BINARIES = ("terraform", "tofu")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tfmodcache",
        description="A local, shared module cache for Terraform and OpenTofu.",
    )
    parser.add_argument("--version", action="version", version="tfmodcache {}".format(__version__))
    parser.add_argument("--cache-dir", metavar="PATH", help="where the mirror lives")
    parser.add_argument("--offline", action="store_true", help="never touch the network")
    parser.add_argument("--timeout", type=float, default=30.0, metavar="SECONDS")
    parser.add_argument("-q", "--quiet", action="store_true")
    sub = parser.add_subparsers(dest="command")

    for name, help_text in (
        ("warm", "download every declared module into the cache"),
        ("link", "lay .terraform/modules out from the cache"),
    ):
        child = sub.add_parser(name, help=help_text)
        child.add_argument("directory", nargs="?", default=".")
        child.add_argument("--symlink", action="store_true", help="link modules instead of copying them")

    child = sub.add_parser("init", help="link from the cache, then run terraform init")
    child.add_argument("directory", nargs="?", default=".")
    child.add_argument("--symlink", action="store_true")
    child.add_argument("--binary", choices=BINARIES, help="which binary to run")
    child.add_argument("arguments", nargs=argparse.REMAINDER, help="arguments passed on to init")

    child = sub.add_parser("list", help="show what the cache holds")
    child.add_argument("--json", action="store_true")

    child = sub.add_parser("purge", help="remove cached modules")
    child.add_argument("--all", action="store_true", help="remove everything")
    child.add_argument("--older-than", type=float, metavar="DAYS")
    child.add_argument("--dry-run", action="store_true")

    sub.add_parser("path", help="print the cache directory")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    options = parser.parse_args(argv)
    if not options.command:
        parser.print_help()
        return 2
    store = Store(options.cache_dir)
    try:
        return _dispatch(options, store)
    except TfmodcacheError as error:
        sys.stderr.write("tfmodcache: {}\n".format(error))
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        sys.stderr.write("tfmodcache: interrupted\n")
        return 130


def _dispatch(options, store: Store) -> int:
    if options.command == "path":
        print(store.root)
        return 0
    if options.command == "list":
        return _list(options, store)
    if options.command == "purge":
        return _purge(options, store)

    resolver = Resolver(
        store,
        offline=options.offline,
        timeout=options.timeout,
        use_symlinks=getattr(options, "symlink", False),
    )
    directory = os.path.abspath(options.directory)
    if not os.path.isdir(directory):
        sys.stderr.write("tfmodcache: no such directory: {}\n".format(directory))
        return 1

    if options.command == "warm":
        _report(resolver.warm(directory), options.quiet, "cached")
        return 0

    outcome = resolver.link(directory)
    _report(outcome, options.quiet, "linked")
    if options.command == "link":
        return 0
    return _run_init(options, directory)


# -- commands -------------------------------------------------------------


def _list(options, store: Store) -> int:
    entries = store.entries()
    if options.json:
        print(json.dumps([entry.as_json() for entry in entries], indent=2, sort_keys=True))
        return 0
    if not entries:
        print("cache is empty ({})".format(store.root))
        return 0
    width = max(len(entry.source) for entry in entries)
    for entry in entries:
        print(
            "{source:<{width}}  {version:<12}  {size:>9}  {files:>5} files".format(
                source=entry.source,
                width=width,
                version=entry.version or "-",
                size=_human(entry.bytes),
                files=entry.files,
            )
        )
    print("{} modules, {} in {}".format(len(entries), _human(store.total_bytes()), store.root))
    return 0


def _purge(options, store: Store) -> int:
    if options.all:
        if options.dry_run:
            print("would remove {} modules".format(len(store.entries())))
            return 0
        print("removed {} modules".format(store.clear()))
        return 0
    if options.older_than is None:
        sys.stderr.write("tfmodcache: purge needs --all or --older-than DAYS\n")
        return 2
    cutoff = time.time() - options.older_than * 86400
    stale = [entry.key for entry in store.entries() if entry.fetched_at < cutoff]
    if options.dry_run:
        print("would remove {} modules".format(len(stale)))
        return 0
    removed = store.forget(stale) + store.prune_orphans()
    print("removed {} modules".format(removed))
    return 0


def _run_init(options, directory: str) -> int:
    binary = options.binary or os.environ.get("TFMODCACHE_TERRAFORM")
    if not binary:
        binary = next((name for name in BINARIES if shutil.which(name)), None)
    if not binary:
        sys.stderr.write("tfmodcache: neither terraform nor tofu is on PATH\n")
        return 1
    arguments = list(options.arguments or [])
    if arguments and arguments[0] == "--":
        arguments = arguments[1:]
    command = [binary, "init"] + arguments
    if not options.quiet:
        print("tfmodcache: running {}".format(" ".join(command)))
    try:
        return subprocess.call(command, cwd=directory)
    except OSError as error:
        sys.stderr.write("tfmodcache: could not run {}: {}\n".format(binary, error))
        return 1


# -- output ---------------------------------------------------------------


def _report(outcome: Outcome, quiet: bool, verb: str) -> None:
    for note in outcome.skipped:
        sys.stderr.write(
            "tfmodcache: left to terraform: {} ({}): {}\n".format(note.key, note.source, note.reason)
        )
    if quiet:
        return
    print(
        "{} {} modules ({} downloaded, {} already cached)".format(
            verb, outcome.cached_count, len(outcome.fetched), len(outcome.reused)
        )
    )


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return "{:.0f} {}".format(value, unit) if unit == "B" else "{:.1f} {}".format(value, unit)
        value /= 1024
    return "{:.1f} GiB".format(value)  # pragma: no cover
