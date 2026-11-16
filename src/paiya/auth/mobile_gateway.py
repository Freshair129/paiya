"""Opt-in settings for an authenticated HTTPS gateway to the owner API."""

import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass, field

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from starlette.requests import Request

from paiya.auth.request_checks import csrf_token_matches
from paiya.settings import get_database_url

DEVICE_COOKIE = "__Host-paiya_o5_device"
DEVICE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
PREVIEW_GROUP_KEY = "synthetic-demo"
_DNS_NAME = r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]"


@dataclass(frozen=True, slots=True)
class MobileGateway:
    origin: str
    backend_host: str
    proxy_key: str = field(repr=False)
    pairing_code: str = field(repr=False)

    def accepts_proxy(self, request: Request) -> bool:
        keys = request.headers.getlist("x-paiya-proxy-key")
        return (
            request.headers.getlist("host") == [self.backend_host]
            and len(keys) == 1
            and csrf_token_matches(keys[0], self.proxy_key)
        )

    def accepts_origin(self, request: Request) -> bool:
        return request.headers.getlist("origin") == [self.origin]

    def _device_signature(self, payload: str) -> str:
        message = f"paiya-o5-device-v1:{self.origin}:{self.pairing_code}:{payload}".encode("ascii")
        return hmac.digest(bytes.fromhex(self.proxy_key), message, "sha256").hex()

    def issue_device_token(self, *, now: int | None = None) -> str:
        expires = (int(time.time()) if now is None else now) + DEVICE_MAX_AGE_SECONDS
        payload = f"{expires}.{secrets.token_hex(16)}"
        return f"{payload}.{self._device_signature(payload)}"

    def accepts_device(self, request: Request, *, now: int | None = None) -> bool:
        token = request.cookies.get(DEVICE_COOKIE, "")
        if not re.fullmatch(r"[0-9]{1,12}\.[0-9a-f]{32}\.[0-9a-f]{64}", token):
            return False
        expires, nonce, signature = token.split(".")
        current = int(time.time()) if now is None else now
        return int(expires) > current and csrf_token_matches(
            signature, self._device_signature(f"{expires}.{nonce}")
        )


def mobile_gateway() -> MobileGateway | None:
    names = (
        "PAIYA_O5_ORIGIN",
        "PAIYA_O5_BACKEND_HOST",
        "PAIYA_O5_PROXY_KEY",
        "PAIYA_O5_PAIRING_CODE",
    )
    origin, backend_host, proxy_key, pairing_code = (os.environ.get(name, "") for name in names)
    if not any((origin, backend_host, proxy_key, pairing_code)):
        return None
    if not (
        origin.startswith("https://")
        and re.fullmatch(_DNS_NAME, origin.removeprefix("https://"))
        and re.fullmatch(_DNS_NAME, backend_host)
        and origin != f"https://{backend_host}"
        and re.fullmatch(r"[0-9a-f]{64}", proxy_key)
        and re.fullmatch(r"[0-9a-f]{64}", pairing_code)
        and proxy_key != pairing_code
    ):
        raise ValueError("O5 requires two distinct HTTPS hostnames and two distinct 32-byte keys")
    if os.environ.get("PAIYA_DEV_FIXTURE_MODE") == "1":
        raise ValueError("O5 cannot run with the loopback fixture login bypass")
    return MobileGateway(origin, backend_host, proxy_key, pairing_code)


def gateway_from_request(request: Request) -> MobileGateway | None:
    gateway = getattr(request.state, "mobile_gateway", None)
    return gateway if isinstance(gateway, MobileGateway) else None


def check_preview_database_url() -> None:
    message = "O5 requires the dedicated Compose synthetic database"
    try:
        url = make_url(get_database_url())
    except (ArgumentError, ValueError, RuntimeError):
        raise ValueError(message) from None
    if (
        url.drivername != "postgresql+psycopg"
        or url.host != "db"
        or url.port != 5432
        or url.database != "paiya_o5_synthetic"
        or url.username != "paiya_o5"
        or not url.password
        or url.query
    ):
        raise ValueError(message)
