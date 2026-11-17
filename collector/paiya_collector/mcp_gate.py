"""Origin-only local forward proxy, closed until the CDP guard is installed.

No request/response bodies or headers are retained. TLS remains end-to-end.
"""

import asyncio
from urllib.parse import urlsplit, urlunsplit

from paiya_collector.browser_runtime import _http_origin


class StartupGate:
    def __init__(self, policy):
        self.policy = policy
        self.armed = False
        self.server = None
        self.writers = set()
        self.tasks = set()
        self.blocked = 0
        self.blocked_source = 0

    async def start(self):
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0, limit=16384)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _client(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.add(writer)
        upstream = None
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            lines = header.split(b"\r\n")
            method, target, _ = lines[0].decode("ascii").split(" ", 2)
            url = "https://" + target if method == "CONNECT" else target
            parsed = urlsplit(url)
            permitted = (
                self.armed
                and not self.policy.failed
                and not self.policy.limited
                and not parsed.username
                and not parsed.password
                and "\\" not in url
                and "%" not in parsed.netloc
                and _http_origin(url) == self.policy.origin
            )
            if not permitted:
                self.blocked += 1
                if _http_origin(url) == self.policy.origin:
                    self.blocked_source += 1
                writer.write(
                    b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                await writer.drain()
                return
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            remote, upstream = await asyncio.wait_for(
                asyncio.open_connection(parsed.hostname, port, limit=65536), 5
            )
            self.writers.add(upstream)
            if method == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            else:
                path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
                forwarded = [f"{method} {path} HTTP/1.1".encode("ascii")]
                forwarded += [
                    line
                    for line in lines[1:]
                    if line
                    and line.split(b":", 1)[0].lower()
                    not in {b"proxy-authorization", b"proxy-connection", b"connection", b"host"}
                ]
                forwarded += [
                    f"Host: {parsed.netloc}".encode("ascii"),
                    b"Connection: close",
                    b"",
                    b"",
                ]
                upstream.write(b"\r\n".join(forwarded))
                await upstream.drain()

            async def pump(source, destination):
                while self.armed and not self.policy.failed and not self.policy.limited:
                    data = await source.read(65536)
                    if not data or not self.armed or self.policy.failed or self.policy.limited:
                        return
                    destination.write(data)
                    await destination.drain()

            forward = asyncio.create_task(pump(reader, upstream))
            backward = asyncio.create_task(pump(remote, writer))
            try:
                await asyncio.wait({forward, backward}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                forward.cancel()
                backward.cancel()
                await asyncio.gather(forward, backward, return_exceptions=True)
        except (Exception, asyncio.CancelledError) as exc:
            self.last_error = type(exc).__name__
        finally:
            for stream in (writer, upstream):
                if stream is not None:
                    stream.close()
                    self.writers.discard(stream)
            self.tasks.discard(task)

    async def close(self):
        self.armed = False
        if self.server is not None:
            self.server.close()
        for writer in tuple(self.writers):
            writer.close()
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
            except TimeoutError:
                pass
        if self.server is not None:
            try:
                await asyncio.wait_for(self.server.wait_closed(), 2)
            except TimeoutError:
                # The listener is closed, forwarding is disabled, and every pump
                # is cancelled. Windows can retain reset-transport accounting.
                pass
