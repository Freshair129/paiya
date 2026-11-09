"""Host and browser-origin checks for every loopback HTTP request."""

import os
from urllib.parse import urlsplit

from starlette.requests import Request

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _port() -> int | None:
    raw_port = os.environ.get("PAIYA_PORT", "8000")
    try:
        port = int(raw_port)
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def _authority_allowed(authority: str) -> bool:
    port = _port()
    if port is None:
        return False
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
    return authority.casefold() in allowed


def host_is_allowed(request: Request) -> bool:
    """Require exactly one allowlisted Host authority with the listener port."""
    values = request.headers.getlist("host")
    return len(values) == 1 and _authority_allowed(values[0])


def origin_is_allowed(request: Request) -> bool:
    """Require an exact HTTP origin matching this request's Host authority."""
    values = request.headers.getlist("origin")
    hosts = request.headers.getlist("host")
    if len(values) != 1 or len(hosts) != 1:
        return False
    origin = values[0]
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme.casefold() != "http"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port != _port()
    ):
        return False
    origin_authority = parsed.netloc
    return (
        _authority_allowed(origin_authority) and origin_authority.casefold() == hosts[0].casefold()
    )
