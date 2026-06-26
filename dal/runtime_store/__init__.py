"""Runtime-store compatibility adapters backed by DAL repositories."""

from dal.runtime_store.app_shell_store import AppShellRepositoryStore

# Temporary compatibility alias for container wiring until the Task 4 DAL rename.
RuntimeRepositoryStore = AppShellRepositoryStore

__all__ = ["AppShellRepositoryStore", "RuntimeRepositoryStore"]
