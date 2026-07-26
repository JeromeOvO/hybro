# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.4] - 2026-07-26

### Added

- Release version control files (`release-please-config.json`, `.release-please-manifest.json`, `VERSION`, `CHANGELOG.md`, `.github/workflows/release-please.yml`)
- Hybro AI open core homepage and unified portal shell
- Webhook signing key generation and verification on install

### Fixed

- Preserve uncertain file finalization and close prepared streams deterministically
- Authenticate webhooks prior to parsing bodies
- Harden artifact materialization limits
