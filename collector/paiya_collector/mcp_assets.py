"""Bounded static-asset relay; browser credentials never enter this transport."""

import asyncio
import base64
import zlib
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

from paiya_collector.browser_runtime import BrowserAdapterError, _http_origin

MAX_ASSET_BYTES = 8 * 1024 * 1024


async def fetch_asset(url, *, policy, resource_type):
    import httpx

    source = urlsplit(policy.group_url)
    source_origin = f"{source.scheme}://{source.netloc}"
    seen = set()
    async with asyncio.timeout(12):
        for _ in range(4):
            parsed = urlsplit(url)
            if (
                url in seen
                or "\\" in url
                or "%" in parsed.netloc
                or not parsed.hostname
                or not parsed.hostname.isascii()
                or parsed.fragment
                or _http_origin(url) not in policy.asset_origins
                or not policy.allow(url, document=False, resource_type=resource_type)
            ):
                raise BrowserAdapterError("static asset redirect was denied")
            seen.add(url)
            # New client per hop means even a redirect's Set-Cookie cannot affect
            # a later request. No browser headers, credentials, proxy or env trust.
            async with httpx.AsyncClient(
                trust_env=False,
                follow_redirects=False,
                headers={"Accept-Encoding": "gzip, deflate, identity", "Origin": source_origin},
                timeout=httpx.Timeout(5, connect=3),
            ) as client:
                async with client.stream("GET", url) as response:
                    policy.response(
                        response.status_code, response.headers.get("retry-after"), datetime.now(UTC)
                    )
                    policy.check()
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise BrowserAdapterError("static asset redirect lacks a destination")
                        url = urljoin(url, location)
                        continue
                    if response.status_code != 200:
                        raise BrowserAdapterError("static asset was unavailable")
                    encoding = response.headers.get("content-encoding", "identity").lower()
                    if encoding not in {"identity", "gzip", "deflate"}:
                        raise BrowserAdapterError("unsupported static asset encoding")
                    decoder = (
                        zlib.decompressobj(31 if encoding == "gzip" else zlib.MAX_WBITS)
                        if encoding != "identity"
                        else None
                    )
                    declared = response.headers.get("content-length")
                    if declared is not None and (
                        not declared.isdigit() or int(declared) > MAX_ASSET_BYTES
                    ):
                        raise BrowserAdapterError("static asset exceeds its byte budget")
                    mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    valid = {
                        "Image": mime.startswith("image/"),
                        "Stylesheet": mime == "text/css",
                        "Script": mime
                        in {
                            "text/javascript",
                            "application/javascript",
                            "application/ecmascript",
                            "text/ecmascript",
                        },
                        "Font": mime.startswith("font/")
                        or mime in {"application/font-woff", "application/vnd.ms-fontobject"},
                    }.get(resource_type, False)
                    if not valid:
                        raise BrowserAdapterError(
                            "static asset MIME does not match its resource type"
                        )
                    chunks = []
                    size = 0
                    decoded_size = 0
                    async for chunk in response.aiter_raw(chunk_size=65536):
                        policy.check()
                        size += len(chunk)
                        if size > MAX_ASSET_BYTES:
                            raise BrowserAdapterError("static asset exceeds its byte budget")
                        if decoder is not None:
                            try:
                                chunk = decoder.decompress(
                                    chunk, MAX_ASSET_BYTES - decoded_size + 1
                                )
                            except zlib.error as exc:
                                raise BrowserAdapterError(
                                    "invalid compressed static asset"
                                ) from exc
                            if decoder.unconsumed_tail or decoder.unused_data:
                                raise BrowserAdapterError("static asset exceeds its decoded budget")
                        decoded_size += len(chunk)
                        if decoded_size > MAX_ASSET_BYTES:
                            raise BrowserAdapterError("static asset exceeds its decoded budget")
                        chunks.append(chunk)
                    if decoder is not None and not decoder.eof:
                        raise BrowserAdapterError("incomplete compressed static asset")
                    body = b"".join(chunks)
                    # Preserve browser-enforced CORS/CORP restrictions, never
                    # fabricate a permissive policy or forward authentication state.
                    headers = [
                        {"name": name, "value": response.headers[name]}
                        for name in (
                            "Access-Control-Allow-Origin",
                            "Access-Control-Allow-Credentials",
                            "Cross-Origin-Resource-Policy",
                            "X-Content-Type-Options",
                        )
                        if name in response.headers
                    ]
                    return {
                        "responseCode": 200,
                        "responseHeaders": [
                            {"name": "Content-Type", "value": response.headers["content-type"]},
                            {"name": "Content-Length", "value": str(len(body))},
                            # Authenticated browser responses must never populate this cache.
                            {"name": "Cache-Control", "value": "no-store"},
                        ]
                        + headers,
                        "body": base64.b64encode(body).decode("ascii"),
                    }
    raise BrowserAdapterError("static asset exceeded its redirect budget")
