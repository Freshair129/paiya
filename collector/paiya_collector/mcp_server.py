"""Restricted Collector MCP service. Start with python -m paiya_collector.mcp_server."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from paiya_collector.browser_adapter import RateLimitPause
from paiya_collector.mcp_browser import GuardedBrowser

runtime = GuardedBrowser()


@asynccontextmanager
async def lifespan(_server):
    try:
        yield {}
    finally:
        await runtime.close()


mcp = FastMCP("paiya-collector-browser", lifespan=lifespan)


async def operation(function, *args, **kwargs):
    async with runtime.lock:
        try:
            value = await function(*args, **kwargs)
            return {"ok": True, "result": value}
        except RateLimitPause:
            return {"ok": False, "reason": "http_429", **runtime.status()}
        except asyncio.CancelledError:
            await asyncio.shield(runtime.close())
            raise
        except Exception:
            # Never leak CDP exceptions, URLs, response headers, cookies or text.
            if runtime.policy is not None:
                runtime.policy.failed = True
            await runtime.close()
            return {"ok": False, "reason": "browser_guard_failed"}


@mcp.tool()
async def collector_open(
    profile_path: str,
    group_url: str,
    selectors: dict[str, str],
    synthetic: bool = False,
    asset_origins: list[str] | None = None,
) -> dict:
    """Open one marked profile with an immutable group and selector configuration."""
    return await operation(
        runtime.open,
        Path(profile_path),
        group_url=group_url,
        selectors=selectors,
        synthetic=synthetic,
        asset_origins=asset_origins or (),
    )


@mcp.tool()
async def collector_navigate() -> dict:
    """Navigate only to the configured group URL."""
    if runtime.policy is None:
        return {"ok": False, "reason": "not_open"}
    return await operation(runtime.navigate, runtime.policy.group_url)


@mcp.tool()
async def collector_snapshot() -> dict:
    """Read the security state, visible cards and bound evidence in one operation."""
    return await operation(runtime.snapshot)


@mcp.tool()
async def collector_scroll() -> dict:
    """Scroll one bounded step on the same guarded document."""
    return await operation(runtime.scroll)


@mcp.tool()
async def collector_status() -> dict:
    """Return only the rate-limit/guard state, never network data."""
    async with runtime.lock:
        return {"ok": True, "result": runtime.status()}


@mcp.tool()
async def collector_close() -> dict:
    """Close only the browser process owned by this service."""
    async with runtime.lock:
        return {"ok": True, "result": await runtime.close()}


if __name__ == "__main__":
    mcp.run(transport="stdio")
