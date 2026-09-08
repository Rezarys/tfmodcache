import os
import time

from tfmodcache.store import Store, default_root, digest_for


def stage_with(store, name, content="x"):
    staged = store.stage()
    with open(os.path.join(staged, name), "w", encoding="utf-8") as handle:
        handle.write(content)
    return staged


def test_commit_then_get(store):
    entry = store.commit("registry/a/b/c/1.0.0", stage_with(store, "main.tf"), "a/b/c", "1.0.0", "url")
    assert entry.files == 1
    assert entry.bytes == 1
    again = store.get("registry/a/b/c/1.0.0")
    assert again is not None
    assert again.version == "1.0.0"
    assert os.path.isfile(os.path.join(again.path, "main.tf"))


def test_missing_key_reads_as_absent(store):
    assert store.get("registry/a/b/c/9.9.9") is None


def test_entry_whose_directory_vanished_is_absent(store):
    store.commit("k", stage_with(store, "main.tf"), "a/b/c", "1.0.0", "url")
    import shutil

    shutil.rmtree(store.path_for("k"))
    assert store.get("k") is None


def test_commit_replaces_a_previous_copy(store):
    store.commit("k", stage_with(store, "old.tf"), "a/b/c", "1.0.0", "url")
    store.commit("k", stage_with(store, "new.tf"), "a/b/c", "1.0.0", "url")
    files = os.listdir(store.path_for("k"))
    assert files == ["new.tf"]


def test_forget_and_clear(store):
    store.commit("one", stage_with(store, "a.tf"), "a/b/c", "1", "u")
    store.commit("two", stage_with(store, "b.tf"), "d/e/f", "2", "u")
    assert store.forget(["one"]) == 1
    assert [entry.key for entry in store.entries()] == ["two"]
    assert store.clear() == 1
    assert store.entries() == []


def test_prune_orphans_removes_both_directions(store):
    store.commit("one", stage_with(store, "a.tf"), "a/b/c", "1", "u")
    os.makedirs(os.path.join(store.modules_root, "deadbeef"), exist_ok=True)
    import shutil

    shutil.rmtree(store.path_for("one"))
    assert store.prune_orphans() == 2
    assert store.entries() == []


def test_index_survives_a_corrupt_file(store):
    store.commit("one", stage_with(store, "a.tf"), "a/b/c", "1", "u")
    with open(store.index_path, "w", encoding="utf-8") as handle:
        handle.write("not json")
    assert store.load() == {}


def test_total_bytes(store):
    store.commit("one", stage_with(store, "a.tf", "hello"), "a/b/c", "1", "u")
    assert store.total_bytes() == 5


def test_fetched_at_is_recorded(store):
    before = time.time()
    entry = store.commit("one", stage_with(store, "a.tf"), "a/b/c", "1", "u")
    assert entry.fetched_at >= before


def test_default_root_honours_the_environment(monkeypatch):
    monkeypatch.setenv("TFMODCACHE_HOME", "/tmp/somewhere")
    assert default_root() == "/tmp/somewhere"
    monkeypatch.delenv("TFMODCACHE_HOME")
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg")
    assert default_root() == os.path.join("/tmp/xdg", "tfmodcache")


def test_digest_is_stable_and_short():
    assert digest_for("k") == digest_for("k")
    assert len(digest_for("k")) == 32


def test_store_without_root_uses_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv("TFMODCACHE_HOME", str(tmp_path / "here"))
    assert Store().root == str(tmp_path / "here")
