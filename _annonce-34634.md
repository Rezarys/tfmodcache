For anyone who lands here from a search: I wrote a small tool that does the module half of what this issue asks for, outside Terraform, while the issue stays open.

`tfmodcache` keeps one copy of each module on disk, keyed by source and exact version, then lays it out in `.terraform/modules` and writes the manifest, so `terraform init` has nothing left to download.

```
pip install tfmodcache==0.1.0
tfmodcache init
```

https://github.com/Rezarys/tfmodcache

It caches registry modules pinned to an exact version and git modules, nested ones included. It deliberately leaves everything else to Terraform, with a message on stderr saying so: version ranges such as `~> 5.0`, source types it does not implement, providers (`TF_PLUGIN_CACHE_DIR` already covers those), and `.tf.json` files. Anything left alone is downloaded exactly as before, so the worst case of installing it is the behaviour you already had.

One thing stated plainly, because it matters to anyone deciding whether to try it: the manifest format follows the documented layout and the registry protocol was checked against a real registry, but running `terraform init` end to end against a manifest this tool writes has not been verified yet. If the manifest is ever wrong, Terraform falls back to downloading the module. Reports of it working, or not working, are what I am after.

MIT, no dependencies, Python 3.9 or newer. It works with OpenTofu through `--binary tofu`.
