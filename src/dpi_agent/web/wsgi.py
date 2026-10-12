"""
WSGI entry point: ``app``.

Vercel runs it through api/index.py.  Any other WSGI server (for example
gunicorn or waitress) can serve ``dpi_agent.web.wsgi:app`` the same way.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs

from dpi_agent.web.api import MAX_BODY_BYTES, Request, dispatch, parse_int


def app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    length = parse_int(environ.get("CONTENT_LENGTH"), 0, 0, MAX_BODY_BYTES + 1)
    too_large = length > MAX_BODY_BYTES
    body = environ["wsgi.input"].read(length) if 0 < length <= MAX_BODY_BYTES else b""

    headers = {
        key[5:].replace("_", "-").lower(): value
        for key, value in environ.items()
        if key.startswith("HTTP_")
    }
    if environ.get("CONTENT_TYPE"):
        headers["content-type"] = environ["CONTENT_TYPE"]

    method = environ.get("REQUEST_METHOD", "GET").upper()
    response = dispatch(
        Request(
            method=method,
            path=environ.get("PATH_INFO") or "/",
            query=parse_qs(environ.get("QUERY_STRING", "")),
            headers=headers,
            body=body,
            client_ip=environ.get("REMOTE_ADDR", ""),
            body_too_large=too_large,
        )
    )
    start_response(
        f"{response.status} {HTTPStatus(response.status).phrase}", response.header_list()
    )
    return [b"" if method == "HEAD" else response.body]
