"""
Local development server (Python standard library only).

Adapts ``http.server`` requests to ``web.api.dispatch``.  Binds to
127.0.0.1 by default and is meant for one local user; for a public
deployment use the WSGI app in web/wsgi.py (see api/index.py for Vercel).
"""

from __future__ import annotations

import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from dpi_agent.registries.database import ensure_database_ready
from dpi_agent.web.api import MAX_BODY_BYTES, Request, dispatch, parse_int

logger = logging.getLogger(__name__)


class LocalRequestHandler(BaseHTTPRequestHandler):
    server_version = "dpi-agent"
    sys_version = ""

    def _handle(self) -> None:
        length = parse_int(self.headers.get("Content-Length"), 0, 0, MAX_BODY_BYTES + 1)
        too_large = length > MAX_BODY_BYTES
        body = self.rfile.read(length) if 0 < length <= MAX_BODY_BYTES else b""
        url = urlparse(self.path)
        response = dispatch(
            Request(
                method=self.command,
                path=url.path or "/",
                query=parse_qs(url.query),
                headers={k.lower(): v for k, v in self.headers.items()},
                body=body,
                client_ip=self.client_address[0],
                body_too_large=too_large,
            )
        )
        self.send_response(response.status)
        for name, value in response.header_list():
            self.send_header(name, value)
        if too_large:
            self.send_header("Connection", "close")  # the unread body cannot be reused
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _handle

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - base-class signature
        """Send per-request lines to logging (shown with `dpi-agent -v serve`)."""
        logger.info("%s %s", self.address_string(), format % args)


def create_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    """Prepare the database and return a server ready to serve_forever()."""
    ensure_database_ready()
    return ThreadingHTTPServer((host, port), LocalRequestHandler)
