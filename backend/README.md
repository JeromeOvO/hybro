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
