import pytest

from tfmodcache import sources


def test_local_sources():
    for raw in ("./modules/vpc", "../shared", "."):
        assert sources.classify(raw).kind == sources.LOCAL


def test_public_registry_source():
    found = sources.classify("terraform-aws-modules/vpc/aws")
    assert found.kind == sources.REGISTRY
    assert found.host == sources.DEFAULT_REGISTRY_HOST
    assert (found.namespace, found.name, found.provider) == ("terraform-aws-modules", "vpc", "aws")


def test_private_registry_source_with_host_and_port():
    found = sources.classify("registry.example.com:8443/team/base/aws//submodule")
    assert found.kind == sources.REGISTRY
    assert found.host == "registry.example.com:8443"
    assert found.subdir == "submodule"


def test_git_source_with_ref_and_subdirectory():
    found = sources.classify("git::https://example.com/org/repo.git?ref=v1.2.3//modules/db")
    assert found.kind == sources.GIT
    assert found.url == "https://example.com/org/repo.git"
    assert found.ref == "v1.2.3"
    assert found.subdir == "modules/db"


def test_git_source_with_the_ref_after_the_subdirectory():
    """The order Terraform's own documentation uses, and the common one in the wild."""
    found = sources.classify("git::https://example.com/org/repo.git//modules/db?ref=v1.2.3")
    assert found.kind == sources.GIT
    assert found.url == "https://example.com/org/repo.git"
    assert found.ref == "v1.2.3"
    assert found.subdir == "modules/db"


def test_other_query_parameters_survive_a_subdirectory():
    found = sources.classify("git::https://example.com/repo.git//modules/db?depth=1&ref=main")
    assert found.url == "https://example.com/repo.git?depth=1"
    assert found.ref == "main"
    assert found.subdir == "modules/db"


def test_registry_source_with_a_subdirectory_keeps_no_query():
    found = sources.classify("ns/mod/aws//submodule")
    assert found.kind == sources.REGISTRY
    assert found.subdir == "submodule"


def test_github_shorthand_becomes_a_git_url():
    found = sources.classify("github.com/org/repo")
    assert found.kind == sources.GIT
    assert found.url == "https://github.com/org/repo.git"


def test_ssh_git_source():
    found = sources.classify("git@example.com:org/repo.git")
    assert found.kind == sources.GIT
    assert found.url == "git@example.com:org/repo.git"


def test_other_query_parameters_survive_ref_removal():
    found = sources.classify("git::https://example.com/repo.git?depth=1&ref=main")
    assert found.url == "https://example.com/repo.git?depth=1"
    assert found.ref == "main"


def test_unsupported_schemes_are_named_not_guessed():
    found = sources.classify("s3::https://bucket.s3.amazonaws.com/module.zip")
    assert found.kind == sources.UNSUPPORTED
    assert found.reason


def test_empty_source_is_unsupported():
    assert sources.classify("   ").kind == sources.UNSUPPORTED


def test_cache_key_carries_the_version():
    registry = sources.classify("terraform-aws-modules/vpc/aws")
    assert sources.cache_key(registry, "5.1.0").endswith("/5.1.0")
    assert sources.cache_key(registry, "5.1.0") != sources.cache_key(registry, "5.2.0")


def test_cache_key_refuses_a_source_it_cannot_fetch():
    with pytest.raises(ValueError):
        sources.cache_key(sources.classify("./local"))


def test_split_subdir_ignores_the_scheme_separator():
    assert sources.split_subdir("https://example.com/a//b") == ("https://example.com/a", "b")
    assert sources.split_subdir("ns/name/prov") == ("ns/name/prov", "")
