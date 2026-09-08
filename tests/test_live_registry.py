"""One test against the real public registry.

It is skipped by default because a test suite should not depend on somebody else's
service. Set ``TFMODCACHE_LIVE=1`` to run it, for example before a release.
"""

import os

import pytest

from tfmodcache.registry import Registry

pytestmark = pytest.mark.skipif(
    os.environ.get("TFMODCACHE_LIVE") != "1", reason="set TFMODCACHE_LIVE=1 to reach the network"
)


def test_the_public_registry_still_speaks_the_protocol_we_implement():
    registry = Registry("registry.terraform.io", timeout=30)
    assert registry.modules_path().endswith("/v1/modules/")
    versions = registry.versions("terraform-aws-modules", "vpc", "aws")
    assert "5.1.0" in versions
    address = registry.download_address("terraform-aws-modules", "vpc", "aws", "5.1.0")
    assert address.startswith("git::") or address.startswith("https://")
