"""Locked, private config/credential files shared by host setup and backend.

Filesystem operations are synchronous for the host CLI. Async callers must run
these bounded operations in a worker thread, not on the request event loop.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import stat
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node

from llm_gateway.runtime_config import (
    CREDENTIAL_ADAPTER,
    ApiKeyCredential,
    OAuthCredential,
    ResolvedCredential,
    RuntimeConfig,
    RuntimeConfigurationError,
    StoredCredential,
    resolve_credential,
)

_MAX_FILE_BYTES = 64 * 1024
_OAUTH_REFRESH_TIMEOUT = 15


async def _store_io[T](operation: Callable[[], T]) -> T:
    # A canceled to_thread still runs. Wait before releasing the directory lock;
    # this also ensures a successful rotating refresh finishes its atomic save.
    return await _complete_before_cancel(
        asyncio.create_task(asyncio.to_thread(operation))
    )


async def _complete_before_cancel[T](task: asyncio.Task[T]) -> T:
    canceled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done() and task.cancelled():
                raise
            canceled = True
        except Exception:
            if canceled:
                raise asyncio.CancelledError from None
            raise
    if canceled:
        raise asyncio.CancelledError
    return result


def runtime_home(environment: Mapping[str, str]) -> Path:
    """One host config location shared by setup and the gateway."""
    configured = environment.get("HYBRO_HOME")
    home = (
        Path(configured)
        if configured is not None
        else Path(environment.get("HOME") or Path.home()) / ".hybro"
    )
    if not home.is_absolute():
        raise RuntimeConfigurationError("Runtime directory must be absolute.")
    return home


def prepare_setup_directory(home: Path) -> bool:
    """Setup-only permission repair; never create a directory or inspect contents.

    lstat gives actionable diagnostics; only the no-follow directory FD authorizes
    repair, so a path replacement cannot redirect chmod through a symlink.
    Runtime readers/writers retain their strict validation without automatic repair.
    """
    try:
        metadata = home.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        raise RuntimeConfigurationError(
            "Runtime directory is a symlink; set HYBRO_HOME to a real directory."
        )
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeConfigurationError(
            "Runtime path is not a directory; set HYBRO_HOME to a private directory."
        )
    ownership_error = (
        "Runtime directory is owned by another user; choose a HYBRO_HOME "
        "you own or ask its administrator to correct ownership."
    )
    if metadata.st_uid != os.geteuid():
        raise RuntimeConfigurationError(ownership_error)
    identity = (metadata.st_dev, metadata.st_ino)
    try:
        fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISDIR(metadata.st_mode):
                raise RuntimeConfigurationError(
                    "Runtime path is not a directory; choose a real HYBRO_HOME directory."
                )
            if metadata.st_uid != os.geteuid():
                raise RuntimeConfigurationError(ownership_error)
            if (metadata.st_dev, metadata.st_ino) != identity:
                raise RuntimeConfigurationError(
                    "Runtime directory changed during setup; rerun setup."
                )
            if stat.S_IMODE(metadata.st_mode) == 0o700:
                return False
            os.fchmod(fd, 0o700)
            return True
        finally:
            os.close(fd)
    except OSError:
        raise RuntimeConfigurationError(
            "Cannot secure runtime directory; check access and ensure HYBRO_HOME "
            "is a real directory you own, then rerun setup."
        ) from None


def load_optional_setup(settings_obj: Any) -> RuntimeState | None:
    """No setup means the original settings contract, not a migration or fallback."""
    environment = dict(os.environ)
    home = runtime_home(environment)
    if not (home / "config.yaml").exists() and not (home / "config.yaml").is_symlink():
        return None
    for provider in ("openai", "deepseek", "anthropic"):
        value = getattr(settings_obj, f"{provider}_api_key", None)
        if value is not None:
            environment[f"{provider.upper()}_API_KEY"] = str(value)
    state = RuntimeConfigStore(home).load(environment)
    from llm_gateway.catalog import validate_models

    validate_models(state.config)
    return state


class _ExpectedStored(Enum):
    UNSET = "unset"


class _ConfigLoader(yaml.SafeLoader):
    """The small config format needs neither aliases nor merge keys."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._depth = 0

    def compose_node(self, parent: Node | None, index: object) -> Node:
        if self.check_event(AliasEvent) or self._depth >= 16:
            raise ValueError("Unsupported YAML structure")
        self._depth += 1
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1

    def construct_mapping(
        self, node: MappingNode, deep: bool = False
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("Duplicate or invalid mapping key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse_config(data: bytes) -> RuntimeConfig:
    try:
        if len(data) > _MAX_FILE_BYTES:
            raise ValueError("File too large")
        value = yaml.load(data.decode("utf-8"), Loader=_ConfigLoader)
        return RuntimeConfig.model_validate(value)
    except (ValueError, UnicodeError, yaml.YAMLError, RecursionError):
        raise RuntimeConfigurationError("Invalid config.yaml; rerun setup.") from None


def _unique_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def parse_credential(data: bytes) -> StoredCredential:
    try:
        if len(data) > _MAX_FILE_BYTES:
            raise ValueError("File too large")
        value = json.loads(data, object_pairs_hook=_unique_json_pairs)
        return CREDENTIAL_ADAPTER.validate_python(value)
    except (ValueError, UnicodeError, RecursionError):
        raise RuntimeConfigurationError("Invalid auth.json; rerun setup.") from None


def _credential_bytes(credential: StoredCredential) -> bytes:
    # Unwrapping secrets is deliberately confined to the private persistence path.
    data = credential.model_dump(mode="json")
    if isinstance(credential, ApiKeyCredential):
        data["api_key"] = credential.api_key.get_secret_value()
    else:
        data["access_token"] = credential.access_token.get_secret_value()
        data["refresh_token"] = credential.refresh_token.get_secret_value()
    encoded = (json.dumps(data, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > _MAX_FILE_BYTES:
        raise RuntimeConfigurationError("Credential exceeds the file size limit.")
    return encoded


@dataclass(frozen=True, slots=True)
class RuntimeState:
    config: RuntimeConfig
    authentication: ResolvedCredential


class RuntimeConfigStore:
    def __init__(self, home: Path) -> None:
        if not home.is_absolute():
            raise RuntimeConfigurationError("Runtime directory must be absolute.")
        self.home = home

    def _open_lock(self) -> int:
        try:
            self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory = self.home.lstat()
            if (
                not stat.S_ISDIR(directory.st_mode)
                or stat.S_IMODE(directory.st_mode) != 0o700
            ):
                raise RuntimeConfigurationError(
                    "Runtime directory must be a real directory with mode 0700."
                )
            fd = os.open(
                self.home / ".runtime.lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
            )
        except OSError:
            raise RuntimeConfigurationError(
                "Cannot open runtime directory lock."
            ) from None
        try:
            lock = os.fstat(fd)
            if not stat.S_ISREG(lock.st_mode) or stat.S_IMODE(lock.st_mode) != 0o600:
                raise RuntimeConfigurationError(
                    "Runtime lock must be a regular file with mode 0600."
                )
            return fd
        except BaseException:
            os.close(fd)
            raise

    @contextmanager
    def _locked(self) -> Iterator[None]:
        fd = self._open_lock()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    @asynccontextmanager
    async def _async_locked(self) -> AsyncIterator[None]:
        # Opening/validating files and flock never run on the event loop. A failed
        # nonblocking attempt closes its FD in the worker, so cancellation cannot
        # leave a worker waiting indefinitely or holding an orphaned lock.
        acquired: list[int] = []

        def attempt() -> None:
            fd = self._open_lock()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
            except BaseException:
                os.close(fd)
                raise
            else:
                acquired.append(fd)

        try:
            async with asyncio.timeout(30):
                while not acquired:
                    await _store_io(attempt)
                    if not acquired:
                        await asyncio.sleep(0.05)
            yield
        finally:
            if acquired:
                await _store_io(lambda: os.close(acquired[0]))

    async def resolve_oauth(
        self,
        expected: RuntimeConfig,
        account: str,
        environment: Mapping[str, str],
        refresh: Callable[[OAuthCredential], Awaitable[OAuthCredential]],
    ) -> OAuthCredential:
        """Reread, serialize refresh and persist rotations without changing revision.

        Config/auth/account changes require a backend restart. Another worker's
        token rotation for the same account is accepted; setup changes are not.
        """

        def snapshot() -> tuple[bytes, bytes, OAuthCredential]:
            config_data, auth_data = self._read("config.yaml"), self._read("auth.json")
            if config_data is None or auth_data is None:
                raise RuntimeConfigurationError(
                    "OAuth configuration missing; rerun setup."
                )
            config = parse_config(config_data)
            credential = resolve_credential(
                config, parse_credential(auth_data), environment
            ).credential
            if (
                config != expected
                or not isinstance(credential, OAuthCredential)
                or credential.account_id != account
            ):
                raise RuntimeConfigurationError(
                    "OAuth selection/account changed; restart backend."
                )
            return config_data, auth_data, credential

        try:
            async with self._async_locked():
                config_data, auth_data, credential = await _store_io(snapshot)
                if credential.expires_at > time.time() + 300:
                    return credential

                async def rotate() -> OAuthCredential:
                    async with asyncio.timeout(_OAUTH_REFRESH_TIMEOUT):
                        updated = await refresh(credential)
                    if (
                        updated.account_id != account
                        or updated.expires_at <= time.time()
                    ):
                        raise RuntimeConfigurationError(
                            "Invalid refreshed OAuth identity or expiry."
                        )

                    def save_rotation() -> None:
                        current_config, current_auth, _ = snapshot()
                        if (current_config, current_auth) != (config_data, auth_data):
                            raise RuntimeConfigurationError(
                                "OAuth state changed during refresh; rerun setup."
                            )
                        self._replace("auth.json", _credential_bytes(updated))

                    await _store_io(save_rotation)
                    return updated

                # A refresh can rotate remotely before its HTTP cleanup returns.
                # Once started, this bounded critical section owns refresh AND
                # persistence. Caller cancellation waits for it under the same
                # lock, then propagates, never discarding a received rotation.
                return await _complete_before_cancel(asyncio.create_task(rotate()))
        except (OSError, TimeoutError):
            raise RuntimeConfigurationError(
                "OAuth refresh/store unavailable; retry setup or restart backend."
            ) from None

    def _read(self, name: str) -> bytes | None:
        try:
            fd = os.open(self.home / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeConfigurationError("Runtime files must be regular files.")
            if name == "auth.json" and stat.S_IMODE(metadata.st_mode) != 0o600:
                raise RuntimeConfigurationError("auth.json must have mode 0600.")
            data = stream.read(_MAX_FILE_BYTES + 1)
        if len(data) > _MAX_FILE_BYTES:
            raise RuntimeConfigurationError("Runtime file exceeds the size limit.")
        return data

    def _replace(self, name: str, data: bytes | None) -> None:
        path = self.home / name
        if data is None:
            path.unlink(missing_ok=True)
        else:
            fd, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=self.home)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        directory_fd = os.open(self.home, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def load(self, environment: Mapping[str, str]) -> RuntimeState:
        """Load one consistent pair; no Provider/network verification occurs here."""
        try:
            with self._locked():
                config_data = self._read("config.yaml")
                if config_data is None:
                    raise RuntimeConfigurationError("config.yaml missing; run setup.")
                config = parse_config(config_data)
                auth_data = self._read("auth.json")
                stored = parse_credential(auth_data) if auth_data is not None else None
                return RuntimeState(
                    config, resolve_credential(config, stored, environment)
                )
        except OSError:
            raise RuntimeConfigurationError(
                "Cannot read runtime configuration."
            ) from None

    def read_stored_credential(self) -> StoredCredential | None:
        """Read setup's credential snapshot without creating an absent directory.

        A concurrent creator is checked by save's expected_stored comparison.
        Existing paths still go through the strict locked reader.
        """
        try:
            self.home.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            raise RuntimeConfigurationError("Cannot read stored credential.") from None
        try:
            with self._locked():
                data = self._read("auth.json")
                return parse_credential(data) if data is not None else None
        except OSError:
            raise RuntimeConfigurationError("Cannot read stored credential.") from None

    def save(
        self,
        config: RuntimeConfig,
        credential: StoredCredential | None,
        environment: Mapping[str, str],
        *,
        expected_stored: StoredCredential
        | None
        | _ExpectedStored = _ExpectedStored.UNSET,
    ) -> bool:
        """Persist an already-verified candidate; return False for semantic no-op.

        The caller owns Provider/model eligibility and live authentication checks.
        Passing None selects environment auth and removes any old stored identity.
        Setup supplies expected_stored (including None for an absent credential)
        to reject changes since verification, before either writing or no-op.
        This is not a crash-atomic two-file transaction: load fails closed when a
        crash leaves a missing or identity-mismatched pair; rerun setup to repair.
        """
        resolve_credential(config, credential, environment)
        config_data = yaml.safe_dump(
            config.model_dump(exclude_none=True), sort_keys=False
        ).encode("utf-8")
        auth_data = _credential_bytes(credential) if credential is not None else None
        try:
            with self._locked():
                old_config = self._read("config.yaml")
                old_auth = self._read("auth.json")
                if expected_stored is not _ExpectedStored.UNSET:
                    current = (
                        parse_credential(old_auth) if old_auth is not None else None
                    )
                    if current != expected_stored:
                        raise RuntimeConfigurationError(
                            "Stored credential changed during setup; rerun setup."
                        )
                if self._unchanged(config, credential, old_config, old_auth):
                    return False
                self._save_pair(config_data, auth_data, old_config, old_auth)
                return True
        except OSError:
            raise RuntimeConfigurationError(
                "Cannot write runtime configuration."
            ) from None

    @staticmethod
    def _unchanged(
        config: RuntimeConfig,
        credential: StoredCredential | None,
        old_config: bytes | None,
        old_auth: bytes | None,
    ) -> bool:
        if old_config is None:
            return False
        try:
            return config == parse_config(old_config) and credential == (
                parse_credential(old_auth) if old_auth is not None else None
            )
        except RuntimeConfigurationError:
            return False

    def _save_pair(
        self,
        config: bytes,
        auth: bytes | None,
        old_config: bytes | None,
        old_auth: bytes | None,
    ) -> None:
        try:
            self._replace("auth.json", auth)
            self._replace("config.yaml", config)
        except OSError:
            try:
                self._replace("auth.json", old_auth)
                self._replace("config.yaml", old_config)
            except OSError:
                raise RuntimeConfigurationError(
                    "Configuration write and rollback failed; rerun setup before start."
                ) from None
            raise RuntimeConfigurationError(
                "Configuration write failed; previous files restored."
            ) from None
