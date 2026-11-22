"""Errors users hit in normal use carry a stable `code` and the `params` its message needs.

`detail` stays an English sentence, so clients that read only `detail` keep working. The web
translates known codes and shows `detail` for anything else (ADR 0046).
"""

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

Params = dict[str, str | int | float]


class CodedHTTPException(HTTPException):
    """An HTTP error with a code a client can translate."""

    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        params: Params | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.params: Params = params or {}


def coded(
    status: int,
    code: str,
    detail: str,
    params: Params | None = None,
    headers: dict[str, str] | None = None,
) -> CodedHTTPException:
    return CodedHTTPException(status, code, detail, params, headers)


async def _coded_response(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, CodedHTTPException):  # registered only for this type
        raise error
    body: dict[str, Any] = {"detail": error.detail, "code": error.code, "params": error.params}
    return JSONResponse(body, status_code=error.status_code, headers=error.headers)


def install_error_handler(app: FastAPI) -> None:
    app.add_exception_handler(CodedHTTPException, _coded_response)
