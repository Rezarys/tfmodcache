import os
import subprocess

import pytest

from tfmodcache import fetch
from tfmodcache.errors import FetchError
from tfmodcache.resolve import MODULES_SUBPATH, Resolver

pytestmark = pytest.mark.skipif(not fetch.git_available(), reason="git is not installed")


def git(arguments, cwd):
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
    )
    subprocess.run(["git"] + arguments, cwd=str(cwd), env=environment, check=True, capture_output=True)


@pytest.fixture
def git_module(tmp_path):
    """A local git repository holding one module and one submodule directory."""
    repository = tmp_path / "origin"
    repository.mkdir()
    git(["init", "--quiet", "-b", "main"], repository)
    (repository / "main.tf").write_text("# root module\n", encoding="utf-8")
    nested = repository / "modules" / "db"
    nested.mkdir(parents=True)
    (nested / "main.tf").write_text("# db module\n", encoding="utf-8")
    git(["add", "-A"], repository)
    git(["commit", "--quiet", "-m", "first"], repository)
    git(["tag", "v1.0.0"], repository)
    return repository


def test_fetch_git_checks_out_the_tag_and_drops_the_history(git_module, tmp_path):
    destination = tmp_path / "out"
    commit = fetch.fetch_git(str(git_module), "v1.0.0", str(destination))
    assert len(commit) == 40
    assert (destination / "main.tf").read_text() == "# root module\n"
    assert not (destination / ".git").exists()


def test_fetch_git_reports_an_unknown_ref(git_module, tmp_path):
    with pytest.raises(FetchError):
        fetch.fetch_git(str(git_module), "v9.9.9", str(tmp_path / "out"))


def test_a_git_module_is_cached_and_linked(project, store, git_module):
    (project / "main.tf").write_text(
        'module "db" {{\n  source = "git::file://{}?ref=v1.0.0//modules/db"\n}}\n'.format(git_module),
        encoding="utf-8",
    )

    outcome = Resolver(store).link(str(project))

    assert outcome.fetched == ["db"]
    assert outcome.skipped == []
    linked = os.path.join(str(project), MODULES_SUBPATH, "db", "modules", "db", "main.tf")
    assert os.path.isfile(linked)


def test_a_git_module_is_reused_on_the_second_run(project, store, git_module):
    (project / "main.tf").write_text(
        'module "db" {{\n  source = "git::file://{}?ref=v1.0.0"\n}}\n'.format(git_module),
        encoding="utf-8",
    )
    Resolver(store).link(str(project))
    assert Resolver(store).link(str(project)).reused == ["db"]


def test_archive_extraction_refuses_a_path_outside_its_directory(tmp_path):
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("../escape.tf")
        info.size = 0
        archive.addfile(info, io.BytesIO(b""))
    with pytest.raises(FetchError):
        fetch._extract_tar(buffer.getvalue(), str(tmp_path))


def test_is_archive():
    assert fetch.is_archive("https://example.com/a.tar.gz")
    assert fetch.is_archive("https://example.com/a.zip?token=1")
    assert not fetch.is_archive("https://example.com/a")
