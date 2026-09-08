import pytest

from tfmodcache.errors import RegistryError
from tfmodcache.registry import Registry, pick_version


def client(registry_server):
    return Registry(registry_server.host, scheme="http", timeout=10)


def test_discovery_and_versions(registry_server):
    registry_server.publish("ns", "mod", "aws", "1.0.0", {"main.tf": ""})
    registry_server.publish("ns", "mod", "aws", "1.1.0", {"main.tf": ""})
    api = client(registry_server)
    assert api.modules_path().endswith("/v1/modules/")
    assert api.versions("ns", "mod", "aws") == ["1.0.0", "1.1.0"]


def test_download_address_is_absolute(registry_server):
    registry_server.publish("ns", "mod", "aws", "1.0.0", {"main.tf": ""})
    address = client(registry_server).download_address("ns", "mod", "aws", "1.0.0")
    assert address.startswith("http://{}/archives/".format(registry_server.host))


def test_unknown_module_is_an_error(registry_server):
    with pytest.raises(RegistryError):
        client(registry_server).versions("ns", "absent", "aws")


def test_unknown_version_is_an_error(registry_server):
    registry_server.publish("ns", "mod", "aws", "1.0.0", {"main.tf": ""})
    with pytest.raises(RegistryError):
        client(registry_server).download_address("ns", "mod", "aws", "9.9.9")


def test_unreachable_registry_is_an_error():
    with pytest.raises(RegistryError):
        Registry("127.0.0.1:1", scheme="http", timeout=2).versions("a", "b", "c")


def test_pick_version_takes_the_newest_without_a_constraint():
    assert pick_version(["1.9.0", "1.10.0", "1.2.0"], None) == "1.10.0"


def test_pick_version_prefers_a_release_over_a_prerelease():
    assert pick_version(["2.0.0-rc1", "2.0.0"], None) == "2.0.0"


def test_pick_version_accepts_an_exact_constraint():
    assert pick_version(["1.0.0", "2.0.0"], "2.0.0") == "2.0.0"
    assert pick_version(["1.0.0", "2.0.0"], "v2.0.0") == "2.0.0"


def test_pick_version_refuses_a_range_rather_than_approximating():
    with pytest.raises(RegistryError):
        pick_version(["1.0.0", "2.0.0"], "~> 1.0")


def test_pick_version_needs_at_least_one_version():
    with pytest.raises(RegistryError):
        pick_version([], None)
