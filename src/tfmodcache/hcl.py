"""A small, tolerant reader for the parts of HCL this tool needs.

It answers one question only: which module blocks does this directory declare, and
with which ``source`` and ``version``. It is deliberately not a full HCL parser.
Anything it cannot understand is skipped and reported, never guessed.
"""

from __future__ import annotations

import os
import re
from typing import Iterator, List, NamedTuple, Optional, Tuple

TF_SUFFIXES = (".tf",)

_MODULE_HEADER = re.compile(r'(?<![\w.-])module\s+"([^"\n]+)"\s*\{')
_STRING_ATTR = re.compile(r'(?m)^[ \t]*(source|version)[ \t]*=[ \t]*"([^"\n]*)"')


class ModuleCall(NamedTuple):
    """One ``module`` block, as written in the configuration."""

    name: str
    source: str
    version: Optional[str]
    file: str

    @property
    def has_version(self) -> bool:
        return bool(self.version)


def mask_comments_and_strings(text: str) -> str:
    """Return *text* with comments blanked out, offsets and line count preserved.

    Quoted strings keep their content: ``source`` values live there. Only the
    comment markers that appear outside a string are neutralised, so a ``#`` in a
    module source does not truncate the block.
    """
    out: List[str] = []
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch == '"':
            out.append(ch)
            i += 1
            while i < length:
                c = text[i]
                out.append(c)
                i += 1
                if c == "\\" and i < length:
                    out.append(text[i])
                    i += 1
                elif c == '"':
                    break
            continue
        if ch == "#" or (ch == "/" and text.startswith("//", i)):
            while i < length and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = length if end == -1 else end + 2
            for c in text[i:end]:
                out.append("\n" if c == "\n" else " ")
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _match_block(text: str, open_brace: int) -> Optional[Tuple[int, int]]:
    """Return the body bounds of the block whose ``{`` is at *open_brace*."""
    depth = 0
    i = open_brace
    length = len(text)
    while i < length:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < length:
                c = text[i]
                i += 1
                if c == "\\":
                    i += 1
                elif c == '"':
                    break
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return open_brace + 1, i
        i += 1
    return None


def iter_module_calls(text: str, filename: str = "<memory>") -> Iterator[ModuleCall]:
    """Yield every ``module`` block declared in one configuration file."""
    masked = mask_comments_and_strings(text)
    for header in _MODULE_HEADER.finditer(masked):
        bounds = _match_block(masked, header.end() - 1)
        if bounds is None:
            continue
        body = masked[bounds[0] : bounds[1]]
        attrs = {}
        for attr in _STRING_ATTR.finditer(body):
            attrs.setdefault(attr.group(1), attr.group(2))
        source = attrs.get("source")
        if not source:
            # A computed or missing source is not something we may guess at.
            continue
        yield ModuleCall(
            name=header.group(1),
            source=source,
            version=attrs.get("version") or None,
            file=filename,
        )


def read_module_calls(directory: str) -> List[ModuleCall]:
    """Read every ``module`` block declared directly in *directory*.

    Subdirectories are not walked: Terraform only loads the ``.tf`` files of the
    directory itself, and so does this reader.
    """
    calls: List[ModuleCall] = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return calls
    for name in names:
        if not name.endswith(TF_SUFFIXES) or name.startswith("."):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            calls.extend(iter_module_calls(handle.read(), name))
    return calls
