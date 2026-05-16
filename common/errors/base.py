from typing import Any


class AppError(Exception):
    code: str
    message: str
    details: dict[str, Any]

    def __init__(
        self,
        message: str,
        code: str = "APP_ERROR",
        details: dict[str, Any] | None = None,
    ):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, entity_type: str, entity_id: str):
        super().__init__(
            f"{entity_type} not found: {entity_id}",
            code="NOT_FOUND",
            details={"entity_type": entity_type, "entity_id": entity_id},
        )


class ValidationError(AppError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, code="VALIDATION", details=details)


class AuthorizationError(AppError):
    def __init__(
        self,
        message: str = "Not authorized",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, code="AUTHORIZATION", details=details)


class ExternalServiceError(AppError):
    def __init__(
        self,
        message: str,
        service: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        error_details = dict(details or {})
        if service is not None:
            error_details["service"] = service
        super().__init__(message, code="EXTERNAL_SERVICE", details=error_details)


HybroError = AppError


class ConflictError(AppError):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, code="CONFLICT", details=details)


class TransientError(AppError):
    retry_after: int | None

    def __init__(
        self,
        message: str,
        retry_after: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.retry_after = retry_after
        error_details = dict(details or {})
        if retry_after is not None:
            error_details["retry_after"] = retry_after
        super().__init__(message, code="TRANSIENT", details=error_details)


class UpstreamError(ExternalServiceError):
    def __init__(
        self,
        message: str,
        service: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, service=service, details=details)
        self.code = "UPSTREAM"


class VectorIndexUnavailableError(ExternalServiceError):
    def __init__(self, index_name: str, operation: str):
        self.index_name = index_name
        self.operation = operation
        super().__init__(
            f"Vector index unavailable for {operation}: {index_name}",
            service="vector_index",
            details={"index_name": index_name, "operation": operation},
        )
        self.code = "VECTOR_INDEX_UNAVAILABLE"


__all__ = [
    "AppError",
    "AuthorizationError",
    "ConflictError",
    "ExternalServiceError",
    "HybroError",
    "NotFoundError",
    "TransientError",
    "UpstreamError",
    "ValidationError",
    "VectorIndexUnavailableError",
]
