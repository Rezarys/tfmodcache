import json
import os
import stat

import pytest

from tfmodcache import cli
from tfmodcache.resolve import MANIFEST_NAME, MODULES_SUBPATH


def run(arguments, cache):
    return cli.main(["--cache-dir", str(cache)] + arguments)


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "cache"


def declare(project, host, name="thing", version="1.0.0"):
    (project / "main.tf").write_text(
        'module "{}" {{\n  source  = "{}/ns/mod/aws"\n  version = "{}"\n}}\n'.format(
            name, host, version
        ),
        encoding="utf-8",
    )


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 2
    assert "usage" in capsys.readouterr().out


def test_path_prints_the_cache_directory(cache, capsys):
    assert run(["path"], cache) == 0
    assert capsys.readouterr().out.strip() == str(cache)


def test_list_on_an_empty_cache(cache, capsys):
    assert run(["list"], cache) == 0
    assert "cache is empty" in capsys.readouterr().out


def test_warm_then_list_then_purge(project, cache, registry_server, capsys):
    registry_server.publish("ns", "mod", "aws", "1.0.0", {"main.tf": "# body\n"})
    declare(project, registry_server.host)

    assert run(["warm", str(project)], cache) == 0
    assert "1 downloaded" in capsys.readouterr().out

    assert run(["list"], cache) == 0
    assert "1.0.0" in capsys.readouterr().out

    assert run(["list", "--json"], cache) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["version"] == "1.0.0"

    assert run(["purge", "--all", "--dry-run"], cache) == 0
    assert "would remove 1" in capsys.readouterr().out
    assert run(["purge", "--all"], cache) == 0
    assert "removed 1" in capsys.readouterr().out


def test_purge_older_than(project, cache, registry_server, capsys):
    registry_server.publish("ns", "mod", "aws", "1.0.0", {"main.tf": ""})
    declare(project, registry_server.host)
    run(["warm", str(project)], cache)
    capsys.readouterr()

    assert run(["purge", "--older-than", "30"], cache) == 0
    assert "removed 0" in capsys.readouterr().out
    assert run(["purge", "--older-than", "0"], cache) == 0
    assert "removed 1" in capsys.readouterr().out


def test_purge_needs_a_selector(cache, capsys):
    assert run(["purge"], cache) == 2
    assert "--all or --older-than" in capsys.readouterr().err


def test_link_writes_the_manifest(project, cache, registry_server):
    registry_server.publish("ns", "mod", "aws", "1.0.0", {"main.tf": ""})
    declare(project, registry_server.host)
    assert run(["link", str(project)], cache) == 0
    manifest = os.path.join(str(project), MODULES_SUBPATH, MANIFEST_NAME)
    with open(manifest, "r", encoding="utf-8") as handle:
        assert [item["Key"] for item in json.load(handle)["Modules"]] == ["", "thing"]


def test_missing_directory_is_an_error(cache, tmp_path, capsys):
    assert run(["link", str(tmp_path / "absent")], cache) == 1
    assert "no such directory" in capsys.readouterr().err


def test_skipped_modules_are_reported_on_stderr(project, cache, capsys):
    (project / "main.tf").write_text(
        'module "bucket" {\n  source = "s3::https://x.s3.amazonaws.com/m.zip"\n}\n', encoding="utf-8"
    )
    assert run(["link", str(project)], cache) == 0
    assert "left to terraform" in capsys.readouterr().err


def test_quiet_keeps_the_summary_out_of_the_way(project, cache, capsys):
    (project / "main.tf").write_text("# nothing\n", encoding="utf-8")
    assert run(["-q", "link", str(project)], cache) == 0
    assert capsys.readouterr().out == ""


def test_init_runs_the_named_binary(project, cache, tmp_path, capsys, monkeypatch):
    fake = tmp_path / "bin" / "terraform"
    fake.parent.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "argv.txt"
    fake.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "{}"\nexit 0\n'.format(marker), encoding="utf-8"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(fake.parent) + os.pathsep + os.environ["PATH"])
    (project / "main.tf").write_text("# nothing\n", encoding="utf-8")

    assert run(["init", str(project), "--", "-upgrade"], cache) == 0
    assert marker.read_text().split() == ["init", "-upgrade"]


def test_init_reports_a_missing_binary(project, cache, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.delenv("TFMODCACHE_TERRAFORM", raising=False)
    (project / "main.tf").write_text("# nothing\n", encoding="utf-8")
    assert run(["init", str(project)], cache) == 1
    assert "neither terraform nor tofu" in capsys.readouterr().err


def test_errors_become_exit_code_one(project, cache, monkeypatch, capsys):
    from tfmodcache.errors import TfmodcacheError

    def boom(self, path):
        raise TfmodcacheError("something went wrong")

    monkeypatch.setattr(cli.Resolver, "link", boom)
    (project / "main.tf").write_text("# nothing\n", encoding="utf-8")
    assert run(["link", str(project)], cache) == 1
    assert "something went wrong" in capsys.readouterr().err


def test_human_sizes():
    assert cli._human(512) == "512 B"
    assert cli._human(2048) == "2.0 KiB"
    assert cli._human(5 * 1024 * 1024) == "5.0 MiB"
