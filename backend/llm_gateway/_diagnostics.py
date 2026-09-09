"""Private, bounded diagnostics: never retain exceptions, bodies or free text."""

import re
from dataclasses import dataclass, replace

_CODES = frozenset(
    {
        "server_error",
        "rate_limit_exceeded",
        "insufficient_quota",
        "usage_limit_reached",
        "usage_not_included",
        "invalid_api_key",
        "invalid_token",
        "token_expired",
        "authentication_error",
        "context_length_exceeded",
        "content_filter",
        "model_not_found",
        "model_not_available",
        "permission_denied",
        "unsupported_model",
        "invalid_request_error",
        "invalid_grant",
        "unsupported_parameter",
        "invalid_type",
        "missing_required_parameter",
    }
)
_HINTS = {
    "authentication": "Check the selected authentication and rerun setup.",
    "model_access": "Check model availability for this account and client; this error alone does not prove an entitlement problem.",
    "permission": "Check account and model access; HTTP 403 alone does not identify the cause.",
    "quota": "Check subscription/API quota before running setup again.",
    "rate_limit": "Wait for the rate limit to reset before running setup again.",
    "provider_5xx": "Provider service failed; try setup later.",
    "network": "Check connectivity to the provider before running setup again.",
    "timeout": "Check connectivity and provider availability before running setup again.",
    "invalid_request": "Check the selected model and adapter request compatibility.",
    "context_overflow": "Reduce request context before trying again.",
    "content_filter": "Provider rejected the content; review the request.",
    "bad_json": "Provider returned invalid JSON; check adapter compatibility.",
    "structured_validation": "Output did not satisfy local structured validation.",
    "stream_validation": "Provider stream was incomplete or invalid; check adapter compatibility.",
    "empty_response": "Provider returned no text; check the selected model and adapter.",
    "local_validation": "Check local model and runtime capability configuration.",
    "unknown": "Check provider availability and local adapter compatibility.",
}
_PARAMETERS = frozenset(
    {
        "input",
        "input.content",
        "input.role",
        "input.type",
        "instructions",
        "model",
        "max_output_tokens",
        "temperature",
        "store",
        "stream",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "text.verbosity",
        "text.format",
        "reasoning.effort",
    }
)

_STAGES = frozenset(
    {"verification", "token_exchange", "refresh", "http", "stream", "structured"}
)


@dataclass(frozen=True, slots=True)
class _Diagnostic:
    stage: str
    category: str
    status: int | None = None
    code: str | None = None
    parameter: str | None = None

    def __post_init__(self) -> None:
        # These values may originate at provider boundaries, including SDK fields.
        object.__setattr__(
            self, "stage", self.stage if self.stage in _STAGES else "verification"
        )
        object.__setattr__(
            self, "category", self.category if self.category in _HINTS else "unknown"
        )
        object.__setattr__(
            self,
            "status",
            self.status
            if type(self.status) is int and 100 <= self.status <= 599
            else None,
        )
        object.__setattr__(self, "code", safe_code(self.code))
        object.__setattr__(self, "parameter", _safe_parameter(self.parameter))

    def describe(self) -> str:
        details = f"stage={self.stage}, category={self.category}"
        if self.status is not None:
            details += f", HTTP {self.status}"
        if self.code is not None:
            details += f", code={self.code}"
        if self.parameter is not None:
            details += f", parameter={self.parameter}"
        return f"{details}. {_HINTS[self.category]}"


def safe_code(value: object) -> str | None:
    return value if isinstance(value, str) and value in _CODES else None


def _http_diagnostic(
    stage: str, status: int | None, code: object = None
) -> _Diagnostic:
    code = safe_code(code)
    category = "unknown"
    if status == 401:
        category = "authentication"
    elif status == 403:
        category = "permission"
    elif status == 429:
        category = "rate_limit"
    elif status is not None and 500 <= status <= 599:
        category = "provider_5xx"
    elif status is not None and 400 <= status <= 499:
        category = "invalid_request"
    category = {
        "model_not_found": "model_access",
        "model_not_available": "model_access",
        "unsupported_model": "model_access",
        "usage_limit_reached": "quota",
        "usage_not_included": "quota",
        "insufficient_quota": "quota",
        "invalid_api_key": "authentication",
        "invalid_token": "authentication",
        "token_expired": "authentication",
        "authentication_error": "authentication",
        "invalid_grant": "authentication",
        "rate_limit_exceeded": "rate_limit",
        "server_error": "provider_5xx",
        "context_length_exceeded": "context_overflow",
        "content_filter": "content_filter",
        "permission_denied": "permission",
    }.get(code, category)
    return _Diagnostic(stage, category, status, code)


def _safe_parameter(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 128:
        return None
    value = re.sub(r"\[\d+\]|\.\d+(?=\.|$)", "", value)
    return value if value in _PARAMETERS else None


def _request_diagnostic(status: int, body: object) -> _Diagnostic:
    """Extract only closed-vocabulary facts, never retain provider free text."""
    if isinstance(body, str):
        body = {"message": body}
    if not isinstance(body, dict):
        return _http_diagnostic("http", status)
    error = body.get("error") or body.get("detail") or body
    if isinstance(error, list):
        error = error[0] if error else {}
    if isinstance(error, str):
        error = {"message": error}
    if not isinstance(error, dict):
        return _http_diagnostic("http", status)
    code = safe_code(error.get("code")) or safe_code(error.get("type"))
    parameter = _safe_parameter(error.get("param"))
    location = error.get("loc")
    if parameter is None and isinstance(location, list) and len(location) <= 8:
        # Validation locations contain only field names and array indices.
        parts = [p for p in location if isinstance(p, str) and p != "body"]
        parameter = _safe_parameter(".".join(parts))
    validation_code = {
        "list_type": "invalid_type",
        "string_type": "invalid_type",
        "missing": "missing_required_parameter",
    }
    kind = error.get("type")
    if isinstance(kind, str):
        code = code or validation_code.get(kind)
    code, parameter = _message_facts(
        error.get("message", error.get("msg")), code, parameter
    )
    return replace(_http_diagnostic("http", status, code), parameter=parameter)


def _message_facts(
    message: object, code: str | None, parameter: str | None
) -> tuple[str | None, str | None]:
    if isinstance(message, str) and len(message) <= 1024:
        # Match known templates; only an allowlisted parameter is ever emitted.
        match = re.match(
            r"^(Unsupported parameter|Missing required parameter|Invalid type for):? ['\"]?([A-Za-z_0-9.\[\]]+)",
            message,
        )
        if match and (known := _safe_parameter(match[2])):
            parameter = parameter or known
            code = (
                code
                or {
                    "Unsupported parameter": "unsupported_parameter",
                    "Missing required parameter": "missing_required_parameter",
                    "Invalid type for": "invalid_type",
                }[match[1]]
            )
        exact = {
            "Input must be a list": ("invalid_type", "input"),
            "Instructions are required": ("missing_required_parameter", "instructions"),
        }.get(message.rstrip("."))
        if exact:
            code, parameter = code or exact[0], parameter or exact[1]
        # Codex can report routing/availability failures as text without a code.
        # Never retain the model/engine name: only emit existing fixed codes.
        if message.startswith("Model not found "):
            code, parameter = code or "model_not_found", parameter or "model"
        elif re.fullmatch(
            r"The ['\"][^'\"]+['\"] model is not supported when using Codex with a ChatGPT account\.?",
            message,
        ):
            code, parameter = code or "unsupported_model", parameter or "model"
    return code, parameter


def _exception_diagnostic(exc: Exception) -> _Diagnostic:
    import json

    import httpx
    from jsonschema.exceptions import SchemaError, ValidationError
    from pydantic import ValidationError as ModelValidationError

    from llm_gateway.errors import LLMModelRoutingError, LLMProviderFailure

    if isinstance(exc, LLMProviderFailure):
        return exc._diagnostic or _Diagnostic(
            "verification", exc.classification.error_class
        )
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return _Diagnostic("verification", "timeout")
    if isinstance(exc, httpx.TransportError):
        return _Diagnostic("verification", "network")
    if isinstance(exc, json.JSONDecodeError):
        return _Diagnostic("verification", "bad_json")
    if isinstance(exc, (ValidationError, SchemaError)):
        return _Diagnostic("verification", "structured_validation")
    if isinstance(exc, (ModelValidationError, LLMModelRoutingError)):
        return _Diagnostic("verification", "local_validation")
    status = getattr(exc, "status_code", None)
    if type(status) is int and 100 <= status <= 599:
        return _http_diagnostic("verification", status, getattr(exc, "code", None))
    # OpenAI wraps transport failures without exposing a numeric status.
    from openai import APIConnectionError, APITimeoutError

    if isinstance(exc, APITimeoutError):
        return _Diagnostic("verification", "timeout")
    if isinstance(exc, APIConnectionError):
        return _Diagnostic("verification", "network")
    return _Diagnostic("verification", "unknown")
