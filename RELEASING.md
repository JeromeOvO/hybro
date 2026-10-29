# Releasing Hybro

This guide is for maintainers. Users install and upgrade Hybro as described in
the [README](README.md).

## Release flow

`release-please` owns version numbers; everything else is produced from them.

1. Merge the release-please PR. It bumps `VERSION`, `backend/pyproject.toml`,
   `frontend/package.json`, and the `packaging/npm/*/package.json` manifests
   together, and creates the `v<version>` GitHub Release.
2. That same workflow then calls the Release workflow as a reusable workflow,
   which publishes for that one version: the container images, the four platform
   CLI bundles plus tarballs and checksums on the Release, and the
   `@hybroai/cli*` npm packages.
3. The final job resolves `docker-compose.release.yml` and confirms every image
   name it references exists, so a stack that points at an unpublished image
   fails the release rather than a user's `hybro start`. It then resolves the
   published `@hybroai/cli` and confirms every platform package its
   `optionalDependencies` name is on the registry. npm skips a missing optional
   dependency silently, so nothing else would catch it: the launcher installs,
   and the wrapper only fails at run time with an "is not installed" message.

The Release workflow is called rather than triggered by `release: published`
because release-please acts with `GITHUB_TOKEN`, and GitHub does not start
workflows for events that token triggers — a release event would never arrive.
`workflow_dispatch` remains for re-publishing after a failure; give it the
version so a retry cannot silently publish whatever `VERSION` happens to say.
The same rule explains why release-please PRs get no automatic CI: GitHub does
start those runs, but holds them until someone with write access approves them.

## Requirements

The workflow needs an `NPM_TOKEN` repository secret with publish rights for the
`@hybroai` scope, and the scope must already exist on npm. Publishing the images
also needs the `ghcr.io/hybroai` packages to be public; GitHub creates them
private, and a private image makes `hybro start` fail with an authorization
error.

`backend/uv.lock` records the project's own version and release-please does not
update it, so a release leaves it one version behind. `uv sync --frozen`
tolerates that, but `uv lock --check` does not.

## Released Compose file

`docker-compose.release.yml` is generated from `docker-compose.yml` by
`default_agents/render_release.py`, which swaps every `build:` block for an
`image:` reference. It is checked in and verified by CI, so the two stacks cannot
drift. The CLI supplies `HYBRO_STACK_TAG` (its own version) and optionally
`HYBRO_IMAGE_REGISTRY` when it starts a released stack, which means a released
install always runs the images published for the CLI that started it.

## Building the CLI locally

Build the CLI locally with `packaging/cli/build.sh`, then stage it for npm with
`packaging/npm/build.sh <darwin-arm64|darwin-x64|linux-arm64|linux-x64>`. Releases
ship a PyInstaller **onefile** executable: a single binary with no symbolic
links, because npm's packer drops links and a macOS onedir bundle depends on
four of them. The bundle extracts to a new temporary directory per run, which is
why the released Compose file pins `name: hybro`.
