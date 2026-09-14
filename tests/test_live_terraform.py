"""End to end tests against a real ``terraform`` binary.

The rest of the suite proves that the layout and the manifest match what Terraform
documents. These tests prove that Terraform itself accepts them, which is a different
claim and the one a user cares about.

All of them are skipped when no binary is on ``PATH``, because a contributor should not
need Terraform installed to run the suite. The git tests need nothing else: they build
their own repository on disk and never leave the machine. The registry test reaches a
service somebody else runs, so it also waits for ``TFMODCACHE_LIVE=1`` and skips again
if the network refuses the call.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

import pytest

from tfmodcache import cli
from tfmodcache.resolve import MANIFEST_NAME, MODULES_SUBPATH

BINARY = os.environ.get("TFMODCACHE_TERRAFORM") or shutil.which("terraform") or shutil.which("tofu")

pytestmark = pytest.mark.skipif(
    BINARY is None, reason="neither terraform nor tofu is on PATH"
)

needs_the_network = pytest.mark.skipif(
    os.environ.get("TFMODCACHE_LIVE") != "1",
    reason="set TFMODCACHE_LIVE=1 to reach the public registry",
)

REGISTRY_MODULE = "hashicorp/dir/template"
REGISTRY_VERSION = "1.0.2"


def network_is_open() -> bool:
    """Ask the registry one cheap question, and treat any refusal as a skip."""
    request = urllib.request.Request(
        "https://registry.terraform.io/.well-known/terraform.json",
        headers={"User-Agent": "tfmodcache-tests"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def run(arguments, cache):
    return cli.main(["--cache-dir", str(cache)] + arguments)


def terraform(directory, *arguments):
    return subprocess.run(
        [BINARY] + list(arguments),
        cwd=str(directory),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
    )


def manifest_of(project):
    path = project / MODULES_SUBPATH / MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def cache(tmp_path):
    return tmp_path / "cache"


@pytest.fixture
def git_module(tmp_path):
    """A real git repository holding one module inside a subdirectory.

    Nothing here leaves the machine: the remote is a path on disk, which git and this
    tool both treat exactly like any other remote.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    repository = tmp_path / "remote"
    write(
        repository / "modules" / "greeting" / "main.tf",
        'variable "name" {\n  type    = string\n  default = "world"\n}\n\n'
        'output "greeting" {\n  value = "hello ${var.name}"\n}\n',
    )
    write(repository / "README.md", "not a module\n")
    for arguments in (
        ["init", "--quiet", "--initial-branch", "main"],
        ["add", "."],
        ["-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "--quiet", "-m", "one"],
        ["tag", "v1"],
    ):
        subprocess.run(
            ["git"] + arguments,
            cwd=str(repository),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    # The order of ``//subdir`` and ``?ref=`` is the one Terraform documents.
    return "git::file://{}//modules/greeting?ref=v1".format(repository)


# -- the registry, against the real one -----------------------------------


@needs_the_network
def test_two_projects_share_one_download_and_terraform_accepts_the_result(tmp_path, cache, capsys):
    if not network_is_open():
        pytest.skip("the network refuses the call to the registry")
    projects = []
    for name in ("a", "b"):
        project = tmp_path / name
        write(
            project / "main.tf",
            'module "files" {{\n  source  = "{}"\n  version = "{}"\n  base_dir = "${{path.module}}"\n}}\n'.format(
                REGISTRY_MODULE, REGISTRY_VERSION
            ),
        )
        projects.append(project)

    assert run(["link", str(projects[0])], cache) == 0
    assert "1 downloaded, 0 already cached" in capsys.readouterr().out

    assert run(["link", str(projects[1])], cache) == 0
    assert "0 downloaded, 1 already cached" in capsys.readouterr().out

    for project in projects:
        entry = next(
            item for item in manifest_of(project)["Modules"] if item["Key"] == "files"
        )
        assert entry["Version"] == REGISTRY_VERSION
        assert (project / entry["Dir"] / "main.tf").is_file()
        completed = terraform(project, "init", "-input=false", "-backend=false")
        assert completed.returncode == 0, completed.stdout.decode("utf-8", "replace")
        output = completed.stdout.decode("utf-8", "replace")
        assert "Downloading" not in output, "terraform downloaded the module again"


# -- git with a subdirectory, and the second pass without a network -------


def test_terraform_accepts_a_git_module_taken_from_a_subdirectory(tmp_path, cache, git_module):
    project = tmp_path / "project"
    write(
        project / "main.tf",
        'module "greeting" {{\n  source = "{}"\n  name   = "terraform"\n}}\n'.format(git_module),
    )

    assert run(["link", str(project)], cache) == 0
    entry = next(item for item in manifest_of(project)["Modules"] if item["Key"] == "greeting")
    assert (project / entry["Dir"] / "main.tf").is_file()
    assert not (project / entry["Dir"] / "README.md").exists(), "the whole repository was laid out"

    completed = terraform(project, "init", "-input=false", "-backend=false")
    assert completed.returncode == 0, completed.stdout.decode("utf-8", "replace")
    completed = terraform(project, "validate")
    assert completed.returncode == 0, completed.stdout.decode("utf-8", "replace")


def test_a_second_project_is_laid_out_with_the_network_forbidden(tmp_path, cache, git_module, capsys):
    first = tmp_path / "first"
    write(first / "main.tf", 'module "greeting" {{\n  source = "{}"\n}}\n'.format(git_module))
    assert run(["warm", str(first)], cache) == 0
    assert "1 downloaded, 0 already cached" in capsys.readouterr().out

    second = tmp_path / "second"
    write(second / "main.tf", 'module "greeting" {{\n  source = "{}"\n}}\n'.format(git_module))
    assert run(["--offline", "link", str(second)], cache) == 0
    assert "0 downloaded, 1 already cached" in capsys.readouterr().out

    entry = next(item for item in manifest_of(second)["Modules"] if item["Key"] == "greeting")
    assert (second / entry["Dir"] / "main.tf").is_file()
    completed = terraform(second, "init", "-input=false", "-backend=false")
    assert completed.returncode == 0, completed.stdout.decode("utf-8", "replace")
