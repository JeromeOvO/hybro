import asyncio
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.middleware.request_logging import RequestLoggingMiddleware
from common.observability.logging import StructuredFormatter


@pytest.fixture
def app() -> RequestLoggingMiddleware:
    application = FastAPI()

    @application.post("/items/{item_id}")
    async def item(item_id: str):
        return {"item_id": item_id}

    @application.get("/bad-request")
    async def bad_request():
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="invalid")

    @application.get("/explode")
    async def explode():
        raise RuntimeError("private exception text")

    return RequestLoggingMiddleware(application)


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("request-123", "request-123"),
        ("contains spaces", None),
        ("x" * 129, None),
    ],
)
def test_request_id_validation_response_header_and_route_template(
    app,
    caplog,
    supplied,
    expected,
):
    caplog.set_level(logging.INFO, logger="common.middleware.request_logging")
    with TestClient(app) as client:
        response = client.post(
            "/items/abc?secret=PRIVATE_QUERY",
            headers={"X-Request-ID": supplied},
            content="PRIVATE_BODY",
        )

    request_id = response.headers["X-Request-ID"]
    if expected is None:
        assert request_id != supplied
        assert len(request_id) == 36
    else:
        assert request_id == expected
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "http_request_completed"
    ]
    assert len(records) == 1
    assert records[0].route == "/items/{item_id}"
    assert records[0].status == 200
    assert records[0].duration_ms >= 0
    assert records[0].request_id == request_id
    assert records[0].trace_id == request_id
    assert "PRIVATE_BODY" not in records[0].__dict__.values()
    assert "PRIVATE_QUERY" not in records[0].__dict__.values()


def test_missing_request_id_is_generated(app, caplog):
    caplog.set_level(logging.INFO, logger="common.middleware.request_logging")
    with TestClient(app) as client:
        response = client.get("/bad-request")

    assert len(response.headers["X-Request-ID"]) == 36
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "http_request_completed"
    )
    assert record.status == 422
    assert record.outcome == "client_error"


def test_unhandled_exception_returns_request_id_and_logs_once(app, caplog):
    caplog.set_level(logging.INFO, logger="common.middleware.request_logging")
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/explode",
            headers={"X-Request-ID": "explode-request"},
        )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "explode-request"
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "http_request_completed"
    ]
    assert len(records) == 1
    assert records[0].status == 500
    assert records[0].outcome == "error"
    assert records[0].error_type == "RuntimeError"
    assert len(records[0].error_fingerprint) == 16
    assert ":explode" in records[0].error_stack
    formatted = StructuredFormatter(
        output_format="json",
        environment="test",
        service_version="test",
    ).format(records[0])
    assert "private exception text" not in formatted
    assert json.loads(formatted)["error_type"] == "RuntimeError"


def test_unhandled_exception_still_propagates_to_test_client(app, caplog):
    caplog.set_level(logging.INFO, logger="common.middleware.request_logging")

    with TestClient(app) as client:
        with pytest.raises(RuntimeError, match="private exception text"):
            client.get("/explode")

    records = [
        record
        for record in caplog.records
        if record.getMessage() == "http_request_completed"
    ]
    assert len(records) == 1
    assert records[0].status == 500


@pytest.mark.asyncio
async def test_post_start_failure_overrides_success_status(caplog):
    private_sentinel = "PRIVATE_POST_START_FAILURE"

    async def downstream(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        raise RuntimeError(private_sentinel)

    middleware = RequestLoggingMiddleware(downstream)
    sent = []
    caplog.set_level(logging.INFO, logger="common.middleware.request_logging")

    async def send(message):
        sent.append(message)

    with pytest.raises(RuntimeError, match=private_sentinel):
        await middleware(
            {
                "type": "http",
                "method": "GET",
                "path": "/stream",
                "headers": [],
            },
            None,
            send,
        )

    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "http_request_completed"
    )
    formatted = StructuredFormatter(
        output_format="json",
        environment="test",
        service_version="test",
    ).format(record)
    assert record.status == 200
    assert record.outcome == "error"
    assert record.error_type == "RuntimeError"
    assert private_sentinel not in formatted


@pytest.mark.asyncio
async def test_request_cancellation_is_logged_and_propagated(caplog):
    async def downstream(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        raise asyncio.CancelledError

    middleware = RequestLoggingMiddleware(downstream)
    caplog.set_level(logging.INFO, logger="common.middleware.request_logging")

    async def send(_message):
        return None

    with pytest.raises(asyncio.CancelledError):
        await middleware(
            {
                "type": "http",
                "method": "GET",
                "path": "/stream",
                "headers": [],
            },
            None,
            send,
        )

    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "http_request_completed"
    )
    assert record.status == 200
    assert record.outcome == "cancelled"
    assert record.error_type == "CancelledError"
