# tfmodcache

```
pip install tfmodcache==0.1.0
```

A local, shared module cache for Terraform and OpenTofu. `terraform init` downloads the same modules again for every repository and every CI job; `tfmodcache` keeps one copy on disk and lays it out where Terraform looks, so the second run and every run after it costs no network at all.

There is a plugin cache for providers (`TF_PLUGIN_CACHE_DIR`). There is none for modules. This is that missing cache, and nothing else.

## Use it

Warm the cache and run `init` in one step:

```
$ tfmodcache init
linked 4 modules (4 downloaded, 0 already cached)
tfmodcache: running terraform init
```

Terraform's own output follows, unaffected by this tool.

Run it again, in this repository or in any other one that uses the same modules:

```
$ tfmodcache init
linked 4 modules (0 downloaded, 4 already cached)
```

## Commands

- `tfmodcache init [DIR] [-- ARGS]` lays the modules out from the cache, then runs `terraform init` with `ARGS`.
- `tfmodcache link [DIR]` lays the modules out and writes the manifest, without running anything.
- `tfmodcache warm [DIR]` fills the cache only, and touches nothing inside the project.
- `tfmodcache list [--json]` shows what the cache holds.
- `tfmodcache purge --all` and `tfmodcache purge --older-than DAYS` remove cached modules.
- `tfmodcache path` prints the cache directory.

Useful flags: `--offline` never touches the network, `--cache-dir PATH` moves the cache, `--symlink` links modules instead of copying them, `--binary tofu` picks OpenTofu.

## In continuous integration

The cache is one directory, so any CI cache action can keep it between runs.

```yaml
- uses: actions/cache@v4
  with:
    path: ~/.cache/tfmodcache
    key: tfmodcache-${{ hashFiles('**/*.tf') }}
- run: pip install tfmodcache==0.1.0
- run: tfmodcache init -- -input=false
```

## What it caches, and what it leaves alone

Cached: registry modules (`namespace/name/provider`, public or private registry, exact `version`) and git modules (`git::`, `github.com/...`, `git@...`, with or without `?ref=` and `//subdir`). Nested modules are followed, including modules declared inside a cached module.

Left to Terraform, on purpose, with a message on stderr saying so:

- version constraints that are not one exact version, such as `~> 5.0`. Terraform owns constraint solving, and a cache that quietly resolved a range differently would be worse than no cache.
- source kinds this tool does not implement, such as `s3::` or `gcs::`.
- providers. `TF_PLUGIN_CACHE_DIR` already covers them, and this tool never touches them.
- `.tf.json` configuration files.

Anything left alone is downloaded by Terraform exactly as before. The worst case of installing this tool is the behaviour you already had.

## Limits

The module layout and the manifest this tool writes follow the format Terraform documents. The module registry protocol has been checked against a real registry. Running `terraform init` itself against the manifest this tool produces has not been verified end to end yet. If the manifest is ever wrong, Terraform falls back to downloading the module itself, so the worst case stays the behaviour you had before installing this tool.

## Where things live

The cache defaults to `~/.cache/tfmodcache`, or `$XDG_CACHE_HOME/tfmodcache`, or `$TFMODCACHE_HOME` when set. Inside a project, `tfmodcache` writes only `.terraform/modules`, which is the directory Terraform itself owns.

## Requirements

Python 3.9 or newer, no third party dependencies. `git` on `PATH` is needed for git module sources.

## Licence

MIT. Written by Younes Z., built with AI assistance, reviewed and tested by me.
