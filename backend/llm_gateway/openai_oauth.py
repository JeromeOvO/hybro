"""Host PKCE login and backend refresh for ChatGPT/Codex subscriptions.

Translated from pi-ai (MIT); see oauth_notice.py. JWT decoding extracts routing
metadata only, never authenticates a Hybro user. All destinations are fixed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import secrets
import shutil
import sys
import time
from collections.abc import Callable, Mapping
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from pydantic import SecretStr

from common.config.runtime_config import (
    OAuthCredential,
    RuntimeConfigurationError,
    account_id,
)
from llm_gateway._diagnostics import _Diagnostic, _http_diagnostic

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_URI = "http://localhost:1455/auth/callback"
TOKEN_URL = "https://auth.openai.com/oauth/token"
LOGIN_TIMEOUT = 300
REFRESH_MARGIN = 300


class _AuthorizationDenied(RuntimeConfigurationError):
    pass


def authorization_flow() -> tuple[str, str, str]:
    verifier = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    state = secrets.token_hex(16)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid profile email offline_access",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "hybro",
        }
    )
    return verifier, state, "https://auth.openai.com/oauth/authorize?" + query


def callback_code(target: str, state: str) -> str:
    """Strict one-time state check; no bare-code/manual state bypass."""
    try:
        url = urlsplit(target)
        if url.scheme or url.netloc or url.path != "/auth/callback" or url.fragment:
            raise ValueError
        query = parse_qs(
            url.query, strict_parsing=True, max_num_fields=16, errors="strict"
        )
        supplied = query.get("state", [])
        if (
            len(supplied) != 1
            or not supplied[0].isascii()
            or not secrets.compare_digest(supplied[0], state)
        ):
            raise ValueError
        if "error" in query:
            raise _AuthorizationDenied("OAuth authorization was denied; rerun setup.")
        codes = query.get("code", [])
        if len(codes) != 1 or not codes[0] or len(codes[0]) > 4096:
            raise ValueError
        return codes[0]
    except _AuthorizationDenied:
        raise
    except ValueError:
        raise RuntimeConfigurationError("Invalid OAuth callback or state.") from None


async def _token(
    fields: Mapping[str, str], *, transport: httpx.AsyncBaseTransport | None = None
) -> OAuthCredential:
    stage = (
        "refresh" if fields.get("grant_type") == "refresh_token" else "token_exchange"
    )
    diagnostic = _Diagnostic(stage, "local_validation")
    status = None
    try:
        async with asyncio.timeout(15):
            async with httpx.AsyncClient(
                transport=transport or httpx.AsyncHTTPTransport(retries=0),
                timeout=15,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    TOKEN_URL,
                    data={"client_id": CLIENT_ID, **fields},
                    headers={"user-agent": "hybro", "accept-encoding": "identity"},
                ) as response:
                    status = response.status_code
                    if status != 200:
                        diagnostic = _http_diagnostic(stage, status)
                        raise ValueError
                    if (
                        response.headers.get("content-encoding", "identity")
                        != "identity"
                    ):
                        raise ValueError
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(body) + len(chunk) > 65536:
                            raise ValueError
                        body.extend(chunk)
                    raw = json.loads(body)
        access, refresh, expiry = (
            raw["access_token"],
            raw["refresh_token"],
            raw["expires_in"],
        )
        if (
            not isinstance(access, str)
            or not isinstance(refresh, str)
            or not refresh.strip()
            or len(refresh) > 16384
            or type(expiry) not in {int, float}
            or not math.isfinite(expiry)
            or expiry <= 0
        ):
            raise ValueError
        return OAuthCredential(
            access_token=SecretStr(access),
            refresh_token=SecretStr(refresh),
            expires_at=time.time() + expiry,
            account_id=account_id(access),
        )
    except json.JSONDecodeError:
        diagnostic = _Diagnostic(stage, "bad_json", status)
    except (httpx.TimeoutException, TimeoutError):
        diagnostic = _Diagnostic(stage, "timeout", status)
    except httpx.HTTPError:
        diagnostic = _Diagnostic(stage, "network", status)
    except (ValueError, KeyError, TypeError, RecursionError, OverflowError):
        pass
    # Raise outside the handler: no HTTP request, tokens or raw body in context.
    raise RuntimeConfigurationError(
        "OAuth token exchange/refresh failed; " + diagnostic.describe()
    )


async def refresh(
    credential: OAuthCredential, *, transport: httpx.AsyncBaseTransport | None = None
) -> OAuthCredential:
    refreshed = await _token(
        {
            "grant_type": "refresh_token",
            "refresh_token": credential.refresh_token.get_secret_value(),
        },
        transport=transport,
    )
    if refreshed.account_id != credential.account_id:
        raise RuntimeConfigurationError(
            "OAuth account changed during refresh; rerun setup."
        )
    return refreshed


async def _open_browser(url: str) -> bool:
    """Bound the OS URL opener itself; no uncancelable executor browser thread."""
    command = "/usr/bin/open" if sys.platform == "darwin" else shutil.which("xdg-open")
    if command is None:
        return False
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            command,
            url,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        async with asyncio.timeout(5):
            return await process.wait() == 0
    except (OSError, TimeoutError):
        return False
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


async def login(  # noqa: C901 - one owner for listener, browser and callback cleanup
    report: Callable[[str], None], *, transport: httpx.AsyncBaseTransport | None = None
) -> OAuthCredential:
    """Five-minute loopback login; Ctrl-C closes listener and pending connections.

    Run setup on the browser's host. If automatic opening fails, open the printed
    URL on that same host. No externally exposed callback or pasted bare codes.
    """
    verifier, state, url = authorization_flow()
    code: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    connections: set[asyncio.Task[None]] = set()

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        connections.add(task)
        try:
            async with asyncio.timeout(5):
                request = await reader.readuntil(b"\r\n\r\n")
                if len(request) > 8192:
                    raise ValueError
                method, target, version = (
                    request.split(b"\r\n", 1)[0].decode("ascii").split(" ")
                )
                if method != "GET" or version != "HTTP/1.1" or code.done():
                    raise ValueError
                result = callback_code(target, state)
                code.set_result(result)
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\nCache-Control: no-store\r\n\r\nHybro authorization received. Return to the terminal.\n"
                )
                await writer.drain()
        except _AuthorizationDenied:
            if not code.done():
                code.set_exception(
                    _AuthorizationDenied("OAuth authorization was denied; rerun setup.")
                )
            writer.write(
                b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\nAuthorization denied.\n"
            )
        except (
            ValueError,
            UnicodeError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            TimeoutError,
            OSError,
        ):
            writer.write(
                b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\nInvalid OAuth callback.\n"
            )
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            connections.discard(task)

    try:
        async with asyncio.timeout(LOGIN_TIMEOUT):
            server = await asyncio.start_server(handle, "127.0.0.1", 1455, limit=8192)
            try:
                async with server:
                    report(
                        "Open this URL on this computer to authorize Hybro (expires in 5 minutes):\n"
                        + url
                    )
                    opened = await _open_browser(url)
                    if not opened:
                        report(
                            "Browser could not open; open the URL above manually on this computer."
                        )
                    result = await code
                    return await _token(
                        {
                            "grant_type": "authorization_code",
                            "code": result,
                            "code_verifier": verifier,
                            "redirect_uri": REDIRECT_URI,
                        },
                        transport=transport,
                    )
            finally:
                server.close()
                await server.wait_closed()
                for task in tuple(connections):
                    task.cancel()
                await asyncio.gather(*connections, return_exceptions=True)
    except TimeoutError:
        raise RuntimeConfigurationError("OAuth login timed out; rerun setup.") from None
    except OSError:
        raise RuntimeConfigurationError(
            "Cannot listen on 127.0.0.1:1455; run setup on your browser's host with that port free."
        ) from None
