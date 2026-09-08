"""The module registry protocol, as the public registry actually answers it.

Checked against ``registry.terraform.io`` on 2026-09-08: service discovery returns
``{"modules.v1": "/v1/modules/"}``, the versions endpoint returns a list, and the
download endpoint answers ``204`` with an ``X-Terraform-Get`` header holding a
go-getter address such as ``git::https://github.com/owner/repo?ref=<commit>``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional

from . import __version__
from .errors import RegistryError

USER_AGENT = "tfmodcache/{}".format(__version__)
DEFAULT_TIMEOUT = 30.0
_DISCOVERY_PATH = "/.well-known/terraform.json"
_FALLBACK_MODULES_PATH = "/v1/modules/"


class Registry:
    """A single module registry host."""

    def __init__(self, host: str, timeout: float = DEFAULT_TIMEOUT, scheme: str = "https"):
        self.host = host
        self.timeout = timeout
        self.scheme = scheme
        self._modules_path: Optional[str] = None

    # -- protocol ---------------------------------------------------------

    def base_url(self) -> str:
        return "{}://{}".format(self.scheme, self.host)

    def modules_path(self) -> str:
        """Service discovery, cached for the lifetime of this object."""
        if self._modules_path is not None:
            return self._modules_path
        try:
            with self._open(self.base_url() + _DISCOVERY_PATH) as response:
                document = json.loads(response.read().decode("utf-8"))
            path = document.get("modules.v1") or _FALLBACK_MODULES_PATH
        except (urllib.error.URLError, ValueError, OSError):
            # A registry without a discovery document still commonly serves /v1/modules/.
            path = _FALLBACK_MODULES_PATH
        if not path.endswith("/"):
            path += "/"
        if path.startswith("/"):
            path = self.base_url() + path
        self._modules_path = path
        return path

    def versions(self, namespace: str, name: str, provider: str) -> List[str]:
        """Every published version, oldest first, as the registry orders them."""
        url = "{}{}/{}/{}/versions".format(self.modules_path(), namespace, name, provider)
        try:
            with self._open(url) as response:
                document = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise RegistryError(
                "registry {} answered {} for {}/{}/{}".format(
                    self.host, error.code, namespace, name, provider
                )
            ) from error
        except (urllib.error.URLError, OSError) as error:
            raise RegistryError("registry {} unreachable: {}".format(self.host, error)) from error
        except ValueError as error:
            raise RegistryError("registry {} returned invalid JSON".format(self.host)) from error
        modules = document.get("modules") or []
        if not modules:
            raise RegistryError("registry {} knows no {}/{}/{}".format(self.host, namespace, name, provider))
        return [item["version"] for item in modules[0].get("versions", []) if item.get("version")]

    def download_address(self, namespace: str, name: str, provider: str, version: Optional[str]) -> str:
        """The go-getter address behind ``X-Terraform-Get``."""
        url = "{}{}/{}/{}".format(self.modules_path(), namespace, name, provider)
        url += "/{}/download".format(version) if version else "/download"
        try:
            with self._open(url) as response:
                address = response.headers.get("X-Terraform-Get")
                body = response.read()
        except urllib.error.HTTPError as error:
            address = error.headers.get("X-Terraform-Get") if error.headers else None
            body = b""
            if not address:
                raise RegistryError(
                    "registry {} answered {} for {}/{}/{} {}".format(
                        self.host, error.code, namespace, name, provider, version or "latest"
                    )
                ) from error
        except (urllib.error.URLError, OSError) as error:
            raise RegistryError("registry {} unreachable: {}".format(self.host, error)) from error
        if not address:
            raise RegistryError(
                "registry {} gave no download address for {}/{}/{}".format(
                    self.host, namespace, name, provider
                )
            )
        del body
        return urllib.parse.urljoin(url, address) if address.startswith("/") else address

    # -- plumbing ---------------------------------------------------------

    def _open(self, url: str):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        return urllib.request.urlopen(request, timeout=self.timeout)


def pick_version(available: List[str], constraint: Optional[str]) -> str:
    """Choose a version the way this tool promises to, and no more.

    An exact version wins. Without a constraint the newest published version wins.
    Ranges such as ``~> 5.0`` are refused rather than approximated: Terraform owns
    constraint solving, and a cache that quietly resolves differently would be worse
    than no cache at all.
    """
    if not available:
        raise RegistryError("the registry published no versions")
    if constraint:
        wanted = constraint.strip()
        if wanted in available:
            return wanted
        if wanted.lstrip("=v ").strip() in available:
            return wanted.lstrip("=v ").strip()
        raise RegistryError(
            "version constraint {!r} is not an exact published version; "
            "tfmodcache caches exact versions only".format(constraint)
        )
    return sorted(available, key=_version_key)[-1]


def _version_key(version: str):
    core = version.split("+", 1)[0]
    core, _, pre = core.partition("-")
    parts = []
    for chunk in core.split("."):
        parts.append(int(chunk) if chunk.isdigit() else 0)
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2], pre == "", pre)
