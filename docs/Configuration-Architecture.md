# Local Configuration Architecture

## Runtime layout

`backend/common/config/loader.py` owns typed defaults and the JSON startup
snapshot. `common/config/cli.py` owns configuration commands and scoped Compose
projections. `settings.py` has been removed. Normal setup/start does not load
repository, backend or frontend dotenv files; Compose explicitly uses
`--env-file /dev/null`. Existing YAML selection and deployment values are imported
only by the explicit `hybro config migrate [--from-env <file>]` command.

Existing `.env`, `.env.example` and frontend environment files are left for the
owner to remove manually. Do not delete or overwrite them during migration.

## Configuration ownership

```text
~/.hybro/config.json ──> thin loader ──> validated startup configuration
~/.hybro/auth.json   ──> credential store ──> authorized consumers only
                                           │
                  CLI startup ─────────────┤
                         ├─ backend configuration
                         ├─ public frontend build configuration
                         └─ scoped Agent connection configuration
```

- `config.json` is the single user configuration source for backend, frontend
  and bundled agents. Users may edit it through the CLI or directly.
- `auth.json` holds credentials. Preserve the existing credential store's file
  locking, safe writes, validation and OAuth refresh ownership; do not create
  another secret store or another refresh owner.
- The default directory is `~/.hybro`. `HYBRO_HOME` is the only location override
  and must be absolute. Do not search project directories or fall back to dotenv.
- Keep the directory private (`0700`) and credentials private (`0600`). Do not
  follow substituted/symlinked runtime files or log secret contents.

## Thin loader

The shared loader owns path resolution, bounded JSON reading, defaults and
validation. The document contains `version`, `provider`, `models`, optional
`backend`/`frontend` overrides and `image_size`. Sensitive backend fields are
rejected in the configuration section; service credentials live under
`auth.json.services` alongside the existing Provider credential fields.

The loader validates:

- JSON syntax, including duplicate keys and non-finite numbers;
- supported field names and schema version;
- types, numeric bounds and required combinations;
- presence of model setup before service startup.

Errors identify the file and safe field path without echoing raw values or
credentials. Missing setup explicitly directs the user to `hybro setup`.
Malformed or incomplete configuration does not select another Provider or read
an old `.env` as a fallback.

Defaults remain in code. The user file need not contain all internal tuning
parameters. Business code consumes a typed configuration object, not arbitrary
`getenv` calls or repeated filesystem reads. Framework metadata such as
`NODE_ENV` and process-manager metadata is not user configuration.

## Editing and lifetime

CLI modifications and startup use the same validation rules. A CLI edit is
validated before it is safely written; a direct JSON edit is validated on the
next startup. Setup/model edits must preserve unrelated application settings,
and credential refresh must preserve unrelated service credentials.

Use one startup snapshot per process. Restart to apply configuration changes.
Do not add filesystem watchers, hot reload, per-project overrides, a configuration
HTTP API, a separate service process, or per-field getter methods.

`hybro config show` displays nonsecret user configuration; `config check` checks
structure without Provider calls. `config set <path> <JSON-value>` validates and
writes deployment overrides under the store lock. `config secret <name>` reads
service credentials without terminal echo. Provider/model selection remains in
`hybro setup`. Start generates missing registrar, inference and webhook secrets
without rotating existing values.

The one-time migration refuses to overwrite `config.json`, preserves Provider
credentials, and optionally imports known legacy deployment values. Environment
files and `config.yaml` remain untouched. This importer is not a runtime fallback.

## Frontend and Docker boundary

Docker Compose and browser bundles cannot consume arbitrary host configuration
as backend Python code does. The CLI/build entry point is responsible for
projecting the validated configuration into the values those processes need.

- Frontend public configuration is an explicit allowlist. Never import the entire
  user config or auth file into client code, and never include server credentials
  in `NEXT_PUBLIC_*` values or other build artifacts.
- `HYBRO_FRONTEND_CONFIG` is the public build projection. `next.config.ts` and
  `src/lib/config-schema.ts` validate it; `src/lib/config.ts` exposes the immutable
  object. SDK `NEXT_PUBLIC_*` constants are derived from this same object.
  Editing JSON does not update an existing build; rebuild when public values change.
- Backend alone mounts the runtime directory. `HYBRO_CONTAINER=1` selects bundled
  Mongo/Redis/file-path/discovery defaults only where JSON has no override.
- Agents keep their SDKs, tool execution, context, HITL and A2A ownership.
  `HYBRO_AGENT_CONFIG` carries only the internal endpoint, inference token and
  image size. `default_agents/runtime_config.py` validates and caches this
  projection; SDK constructors receive explicit values rather than discovering
  dotenv or per-Agent model files. Provider credentials and the runtime mount
  never reach an Agent.
- Environment variables may be an implementation detail of CLI-to-build/container
  transport. They are not a separately maintained configuration source and must
  not cause Compose or Next.js to rediscover old dotenv files implicitly.

## Acceptance checks

Regression coverage must preserve these boundaries:

1. Run the setup/start workflow with isolated HOME and no repository/backend/
   frontend dotenv files or `.env.example`. Missing setup must produce the
   explicit setup error, not an unrelated API-key error.
2. Verify that CLI edits and direct JSON edits resolve to the same settings after
   restart, and that invalid JSON, duplicate/unknown fields and invalid values
   fail with redacted diagnostics.
3. Verify that setup saves and OAuth refresh preserve unrelated config and auth
   fields, including across concurrent writers and failed writes.
4. Verify backend and Agent authentication, container network addresses, and the
   public frontend projection without using real Provider calls in unit tests.
5. Verify frontend build configuration and confirm that secrets are absent from
   browser assets, generated public files and command output.
6. Check all remaining dotenv readers, Compose `env_file` entries, installer
   bootstrap steps and configuration consumers. Documentation changes alone do
   not satisfy these checks.

No real user environment file is removed to perform these tests.
