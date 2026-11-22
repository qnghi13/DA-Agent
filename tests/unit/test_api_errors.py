"""Coded errors: a stable `code` and `params` next to the English `detail`, so the web can
translate the errors users hit (ADR 0046)."""

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from da_agent.api.errors import coded, install_error_handler


def client_for(error: Exception) -> TestClient:
    app = FastAPI()
    install_error_handler(app)

    @app.get("/boom")
    def boom() -> None:
        raise error

    return TestClient(app)


def test_a_coded_error_keeps_detail_and_adds_code_and_params() -> None:
    error = coded(413, "upload.too_many_files", "at most 20 files", {"max": 20})

    response = client_for(error).get("/boom")

    assert response.status_code == 413
    assert response.json() == {
        "detail": "at most 20 files",
        "code": "upload.too_many_files",
        "params": {"max": 20},
    }


def test_a_coded_error_without_params_sends_an_empty_object() -> None:
    response = client_for(coded(404, "not_found", "not found")).get("/boom")

    assert response.json() == {"detail": "not found", "code": "not_found", "params": {}}


def test_headers_survive() -> None:
    error = coded(429, "limit.requests", "slow", headers={"Retry-After": "7"})

    response = client_for(error).get("/boom")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"


def test_plain_http_exceptions_are_unchanged() -> None:
    response = client_for(HTTPException(400, "x")).get("/boom")

    assert response.status_code == 400
    assert response.json() == {"detail": "x"}
