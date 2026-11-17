"""Synchronous Collector adapter over an owned, restricted stdio MCP session."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from paiya_collector.browser_adapter import (
    PageSnapshot,
    RateLimitPause,
    VisibleComment,
    VisiblePost,
)
from paiya_collector.browser_runtime import BrowserAdapterError

TOOLS = frozenset(
    {
        "collector_open",
        "collector_navigate",
        "collector_snapshot",
        "collector_scroll",
        "collector_status",
        "collector_close",
    }
)


class McpConnection:
    def __init__(self):
        self.ready = threading.Event()
        self.error = False
        self.loop = None
        self.stop = None
        self.session = None
        self.thread = threading.Thread(target=self._thread, name="paiya-mcp", daemon=True)

    def _thread(self):
        try:
            asyncio.run(self._serve())
        except BaseException:
            self.error = True
            self.ready.set()

    async def _serve(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self.loop = asyncio.get_running_loop()
        self.stop = asyncio.Event()
        # Deliberately exclude unrelated environment secrets. The child needs no
        # credentials: Chrome owns its session storage; no model receives it.
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "COMSPEC",
                "PATH",
                "PATHEXT",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "LOCALAPPDATA",
                "APPDATA",
                "PROGRAMFILES",
                "PROGRAMFILES(X86)",
            }
        }
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "paiya_collector.mcp_server"],
            env=env,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        with open(os.devnull, "w") as errors:
            async with stdio_client(params, errlog=errors) as (read, write):
                async with ClientSession(read, write) as session:
                    self.session = session
                    await session.initialize()
                    listed = await session.list_tools()
                    if {tool.name for tool in listed.tools} != TOOLS:
                        raise BrowserAdapterError("unexpected MCP tool surface")
                    self.ready.set()
                    await self.stop.wait()
                    try:
                        await asyncio.wait_for(session.call_tool("collector_close", {}), 12)
                    except BaseException:
                        pass

    def __enter__(self):
        self.thread.start()
        if not self.ready.wait(20) or self.error:
            self.__exit__(None, None, None)
            raise BrowserAdapterError("restricted MCP service failed to start")
        return self

    def call(self, name: str, args: dict | None = None):
        if self.error or not self.thread.is_alive() or self.session is None:
            raise BrowserAdapterError("MCP transport is unavailable; checkpoint is unchanged")
        future = asyncio.run_coroutine_threadsafe(
            self.session.call_tool(name, args or {}), self.loop
        )
        try:
            response = future.result(timeout=50)
            if response.isError:
                raise BrowserAdapterError("MCP operation failed")
            value = response.structuredContent
            if value is None:
                value = json.loads(
                    "".join(item.text for item in response.content if item.type == "text")
                )
            if not value.get("ok"):
                if value.get("reason") == "http_429":
                    self._raise_pause(value)
                raise BrowserAdapterError("MCP browser guard failed; checkpoint is unchanged")
            return value["result"]
        except RateLimitPause:
            raise
        except BaseException as exc:
            future.cancel()
            self.error = True
            if self.stop is not None:
                self.loop.call_soon_threadsafe(self.stop.set)
            raise BrowserAdapterError(
                "MCP transport or browser guard failed; checkpoint is unchanged"
            ) from exc

    @staticmethod
    def _raise_pause(status):
        deadline = status.get("retry_after_until")
        raise RateLimitPause(
            datetime.fromisoformat(deadline) if deadline else None,
            manual_resume_required=status.get("manual", deadline is None),
        )

    def __exit__(self, *_):
        if self.stop is not None and self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.stop.set)
        if self.thread.is_alive():
            self.thread.join(timeout=25)
        if self.thread.is_alive():
            raise BrowserAdapterError("MCP service did not close; profile remains protected")


class McpPage:
    def __init__(self, rpc, *, group_url: str):
        self.rpc = rpc
        self.group_url = group_url

    def navigate(self, url: str):
        if url != self.group_url:
            raise BrowserAdapterError("MCP navigation must use the configured group")
        self.rpc("collector_navigate")

    def scroll_one_step(self):
        self.rpc("collector_scroll")

    def raise_if_rate_limited(self):
        status = self.rpc("collector_status")
        if status["rate_limited"]:
            McpConnection._raise_pause(status)
        if status["guard_failed"]:
            raise BrowserAdapterError("MCP browser guard failed; checkpoint is unchanged")

    def snapshot(self, selectors):
        response = self.rpc("collector_snapshot")
        if not response["cards_read"]:
            return PageSnapshot(response["url"], response["body_text"], "", True, ())
        posts = tuple(
            VisiblePost(
                permalink=urljoin(response["url"], card["href"] or ""),
                post_text=card["postText"],
                seller_name=card["sellerName"] or None,
                comments=tuple(
                    VisibleComment(
                        text=comment["text"],
                        author_name=comment["author"] or None,
                        comment_key=comment["key"],
                        source_time=comment["time"],
                    )
                    for comment in card["comments"]
                ),
                pinned=card["pinned"],
                suggested=card["suggested"],
                image_bytes=base64.b64decode(card["image"], validate=True)
                if card["image"]
                else None,
                image_capture_failed=card["imageFailed"],
            )
            for card in response["cards"]["cards"]
        )
        return PageSnapshot(
            response["url"],
            response["body_text"],
            response["cards"]["sortLabel"],
            response["cards"]["feedEnd"],
            posts,
        )


@contextmanager
def attach_mcp_browser(profile_dir, *, config, repository_root):
    with McpConnection() as connection:
        connection.call(
            "collector_open",
            {
                "profile_path": str(profile_dir),
                "group_url": config.group_url,
                "selectors": config.selectors,
                "synthetic": config.synthetic,
                "asset_origins": list(config.asset_origins),
            },
        )
        yield McpPage(connection.call, group_url=config.group_url)
