import asyncio
import time

import httpx
from paiya_collector.mcp_gate import StartupGate
from paiya_collector.mcp_policy import RequestPolicy


def test_gate_denies_before_arm_and_after_close_without_contacting_source():
    async def run():
        hits = []

        async def source(reader, writer):
            hits.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(source, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        url = f"http://localhost:{port}/groups/synthetic"
        gate = StartupGate(RequestPolicy(url, synthetic=True))
        await gate.start()
        try:
            async with httpx.AsyncClient(
                proxy=f"http://127.0.0.1:{gate.port}", trust_env=False
            ) as client:
                assert (await client.get(url)).status_code == 403
                assert not hits and gate.blocked_source == 1
                gate.armed = True
                assert (await client.get(url)).text == "ok"
                assert len(hits) == 1
            idle_reader, idle_writer = await asyncio.open_connection("127.0.0.1", gate.port)
            started = time.monotonic()
            await gate.close()
            assert time.monotonic() - started < 3
            assert not gate.server.is_serving()
            assert await idle_reader.read() == b""
            idle_writer.close()
            assert len(hits) == 1
        finally:
            await gate.close()
            server.close()
            await server.wait_closed()

    asyncio.run(run())
