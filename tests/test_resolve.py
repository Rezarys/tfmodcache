import json
import os

from tfmodcache.resolve import MANIFEST_NAME, MODULES_SUBPATH, Resolver


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def manifest_of(project):
    path = os.path.join(str(project), MODULES_SUBPATH, MANIFEST_NAME)
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)["Modules"]


def by_key(records):
    return {item["Key"]: item for item in records}


def test_registry_module_is_downloaded_linked_and_recorded(project, store, registry_server):
    registry_server.publish("ns", "mod", "aws", "1.0.0", {"main.tf": "# module body\n"})
    write(
        project / "main.tf",
        'module "thing" {{\n  source  = "{}/ns/mod/aws"\n  version = "1.0.0"\n}}\n'.format(
            registry_server.host
        ),
    )

    outcome = Resolver(store).link(str(project))

    assert outcome.fetched == ["thing"]
    assert outcome.skipped == []
    records = by_key(manifest_of(project))
    assert records[""]["Dir"] == "."
    assert records["thing"]["Version"] == "1.0.0"
    assert records["thing"]["Dir"] == os.path.join(MODULES_SUBPATH, "thing")
    assert os.path.isfile(os.path.join(str(project), records["thing"]["Dir"], "main.tf"))


def test_second_run_reuses_the_cache_without_calling_the_registry(project, store, registry_server):
    registry_server.publish("ns", "mod", "aws", "1.0.0", {"main.tf": ""})
    write(
        project / "main.tf",
        'module "thing" {{\n  source  = "{}/ns/mod/aws"\n  version = "1.0.0"\n}}\n'.format(
            registry_server.host
        ),
    )
    Resolver(store).link(str(project))
    registry_server.calls.clear()

    outcome = Resolver(store).link(str(project))

    assert outcome.reused == ["thing"]
    assert outcome.fetched == []
    assert registry_server.calls == []


def test_offline_without_a_cache_leaves_the_module_to_terraform(project, store, registry_server):
    write(
        project / "main.tf",
        'module "thing" {{\n  source  = "{}/ns/mod/aws"\n  version = "1.0.0"\n}}\n'.format(
            registry_server.host
        ),
    )
    outcome = Resolver(store, offline=True).link(str(project))
    assert outcome.cached_count == 0
    assert [note.key for note in outcome.skipped] == ["thing"]
    assert by_key(manifest_of(project)).keys() == {""}


def test_local_modules_are_recorded_and_walked(project, store, registry_server):
    registry_server.publish("ns", "mod", "aws", "1.0.0", {"main.tf": ""})
    write(project / "main.tf", 'module "child" {\n  source = "./modules/child"\n}\n')
    write(
        project / "modules" / "child" / "main.tf",
        'module "deep" {{\n  source  = "{}/ns/mod/aws"\n  version = "1.0.0"\n}}\n'.format(
            registry_server.host
        ),
    )

    outcome = Resolver(store).link(str(project))

    records = by_key(manifest_of(project))
    assert records["child"]["Dir"] == "modules/child"
    assert "Version" not in records["child"]
    assert records["child.deep"]["Dir"] == os.path.join(MODULES_SUBPATH, "child.deep")
    assert outcome.fetched == ["child.deep"]


def test_nested_modules_inside_a_cached_module_are_followed(project, store, registry_server):
    registry_server.publish("ns", "leaf", "aws", "1.0.0", {"main.tf": ""})
    registry_server.publish(
        "ns",
        "mod",
        "aws",
        "1.0.0",
        {
            "main.tf": 'module "leaf" {{\n  source  = "{}/ns/leaf/aws"\n  version = "1.0.0"\n}}\n'.format(
                registry_server.host
            )
        },
    )
    write(
        project / "main.tf",
        'module "top" {{\n  source  = "{}/ns/mod/aws"\n  version = "1.0.0"\n}}\n'.format(
            registry_server.host
        ),
    )

    Resolver(store).link(str(project))

    records = by_key(manifest_of(project))
    assert "top.leaf" in records
    assert os.path.isdir(os.path.join(str(project), records["top.leaf"]["Dir"]))


def test_a_subdirectory_source_points_the_manifest_at_the_subdirectory(project, store, registry_server):
    registry_server.publish(
        "ns", "mod", "aws", "1.0.0", {"main.tf": "", "modules/db/main.tf": "# db\n"}
    )
    write(
        project / "main.tf",
        'module "db" {{\n  source  = "{}/ns/mod/aws//modules/db"\n  version = "1.0.0"\n}}\n'.format(
            registry_server.host
        ),
    )

    Resolver(store).link(str(project))

    records = by_key(manifest_of(project))
    assert records["db"]["Dir"].endswith(os.path.join("db", "modules", "db"))
    assert os.path.isfile(os.path.join(str(project), records["db"]["Dir"], "main.tf"))


def test_a_range_constraint_resolves_through_the_registry(project, store, registry_server):
    registry_server.publish("ns", "mod", "aws", "1.0.0", {"main.tf": ""})
    registry_server.publish("ns", "mod", "aws", "1.4.0", {"main.tf": ""})
    write(
        project / "main.tf",
        'module "thing" {{\n  source  = "{}/ns/mod/aws"\n  version = "~> 1.0"\n}}\n'.format(
            registry_server.host
        ),
    )

    outcome = Resolver(store).link(str(project))

    assert [note.reason for note in outcome.skipped]
    assert outcome.cached_count == 0


def test_an_unsupported_source_is_named_and_left_alone(project, store):
    write(project / "main.tf", 'module "bucket" {\n  source = "s3::https://x.s3.amazonaws.com/m.zip"\n}\n')
    outcome = Resolver(store).link(str(project))
    assert [note.key for note in outcome.skipped] == ["bucket"]
    assert by_key(manifest_of(project)).keys() == {""}


def test_warm_fills_the_cache_without_writing_into_the_project(project, store, registry_server):
    registry_server.publish("ns", "mod", "aws", "1.0.0", {"main.tf": ""})
    write(
        project / "main.tf",
        'module "thing" {{\n  source  = "{}/ns/mod/aws"\n  version = "1.0.0"\n}}\n'.format(
            registry_server.host
        ),
    )

    outcome = Resolver(store).warm(str(project))

    assert outcome.fetched == ["thing"]
    assert not os.path.exists(os.path.join(str(project), ".terraform"))
    assert store.get("registry/{}/ns/mod/aws/1.0.0".format(registry_server.host)) is not None


def test_link_can_use_symlinks(project, store, registry_server):
    registry_server.publish("ns", "mod", "aws", "1.0.0", {"main.tf": ""})
    write(
        project / "main.tf",
        'module "thing" {{\n  source  = "{}/ns/mod/aws"\n  version = "1.0.0"\n}}\n'.format(
            registry_server.host
        ),
    )
    Resolver(store, use_symlinks=True).link(str(project))
    assert os.path.islink(os.path.join(str(project), MODULES_SUBPATH, "thing"))


def test_relinking_over_a_previous_layout_replaces_it(project, store, registry_server):
    registry_server.publish("ns", "mod", "aws", "1.0.0", {"main.tf": ""})
    write(
        project / "main.tf",
        'module "thing" {{\n  source  = "{}/ns/mod/aws"\n  version = "1.0.0"\n}}\n'.format(
            registry_server.host
        ),
    )
    Resolver(store).link(str(project))
    stale = os.path.join(str(project), MODULES_SUBPATH, "thing", "stale.tf")
    with open(stale, "w", encoding="utf-8") as handle:
        handle.write("# left over\n")

    Resolver(store).link(str(project))

    assert not os.path.exists(stale)


def test_a_project_without_modules_still_gets_a_manifest(project, store):
    write(project / "main.tf", 'resource "null_resource" "a" {}\n')
    outcome = Resolver(store).link(str(project))
    assert outcome.records[0].key == ""
    assert manifest_of(project) == [{"Key": "", "Source": "", "Dir": "."}]
