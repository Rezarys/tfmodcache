# Changelog

## 0.1.1, not released yet

- Fix: a git source written the way Terraform documents it, `git::URL//subdir?ref=v1`, lost its `ref` and looked for a subdirectory named after the whole query. Both orders of `//subdir` and `?ref=` now parse the same way.
- End to end tests against a real Terraform binary: a git module taken from a subdirectory, and a second project laid out with the network forbidden, both passing. A third test covers a registry module shared by two projects; it needs network access to run and has only been checked by hand so far.

## 0.1.0

First release.

- Cache registry modules and git modules in one local mirror, keyed by source and exact version.
- `link` lays `.terraform/modules` out from the mirror and writes the Terraform module manifest.
- `init` links, then runs `terraform init` or `tofu init` with any arguments you pass through.
- `warm` fills the mirror without writing anything into the project.
- `list`, `purge --all`, `purge --older-than DAYS`, `path`.
- `--offline` never touches the network; `--symlink` links instead of copying.
- Nested modules are followed, including modules declared inside a cached module.
- Version constraints that are not one exact version are left to Terraform, with a message saying so.
- No third party dependencies.
