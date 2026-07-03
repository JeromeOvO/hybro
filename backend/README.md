# multi-agents-backend

This project is the backend for HybroAI's multi-agents system.

## Getting Started

### 1. Clone the Repository

```sh
git clone git@github.com:hybroai/multi-agents-backend.git
cd ./multi-agents-backend
```

### 2. Install Dependencies

Use [uv](https://github.com/astral-sh/uv) for fast dependency management:

```sh
uv sync
```

### 3. Start the Project

Run the following command to start the backend server:

```sh
uvicorn main:app
```

The project will be accessible at [http://localhost:8000](http://localhost:8000).

---

## Environment Configuration

### A2A Inline File Dispatch Limits

`A2A_INLINE_FILE_MAX_RAW_BYTES` limits the raw bytes for one user-uploaded file
before it is base64 encoded into an outbound A2A message. The default is
`5242880` bytes, or 5 MiB.

`A2A_INLINE_MESSAGE_MAX_ENCODED_BYTES` limits the aggregate base64-encoded file
bytes across all file parts in one outbound A2A message. The default is
`6990508` bytes. When this setting is `0` or blank, the backend derives it from
`A2A_INLINE_FILE_MAX_RAW_BYTES` as `4 * ceil(raw_limit / 3)`.

Treat these values as memory and backpressure controls for inline bytes
dispatch. They are not URI-dispatch feature flags: user-uploaded files that are
sent to agents use inline A2A file bytes, while platform storage URIs stay
internal to Hybro.

---

## Dependency Management with uv

- **Add a new dependency:**

  ```sh
  uv add <package>
  ```

- **Remove a dependency:**

  ```sh
  uv remove <package>
  ```

- **Upgrade all dependencies and update the lockfile:**

  ```sh
  uv lock --upgrade
  ```

- **Sync your environment to the lockfile:**
  ```sh
  uv sync
  ```

---

## Notes

- Always use `uv` commands to manage dependencies to ensure consistency.
- After adding or removing dependencies, remember to run `uv sync` to update your environment.

---

Happy hacking!
