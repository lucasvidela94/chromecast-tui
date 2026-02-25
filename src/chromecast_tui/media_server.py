"""
Local HTTP file server with range-request support so Chromecast can seek.
Runs in a background thread using aiohttp.
"""

import asyncio
import mimetypes
import os
import socket
import threading
from pathlib import Path

from aiohttp import web


def get_local_ip() -> str:
    """Return the machine's LAN IP (the one Chromecast can reach)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


async def handle_media(request: web.Request) -> web.StreamResponse:
    """Serve a file with full range-request support for seeking."""
    rel = request.match_info["path"]
    file_path = Path("/") / rel

    if not file_path.exists() or not file_path.is_file():
        raise web.HTTPNotFound()

    mime, _ = mimetypes.guess_type(str(file_path))
    if mime is None:
        mime = "application/octet-stream"

    file_size = file_path.stat().st_size
    range_header = request.headers.get("Range")

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length",
        "Accept-Ranges": "bytes",
        "Content-Type": mime,
    }

    if range_header:
        # Parse "bytes=start-end"
        range_val = range_header.replace("bytes=", "")
        parts = range_val.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1

        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        headers["Content-Length"] = str(length)

        response = web.StreamResponse(status=206, headers=headers)
        await response.prepare(request)

        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = length
            chunk_size = 65536
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                await response.write(chunk)
                remaining -= len(chunk)
    else:
        headers["Content-Length"] = str(file_size)
        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)

        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                await response.write(chunk)

    await response.write_eof()
    return response


async def handle_options(request: web.Request) -> web.Response:
    """Handle CORS preflight."""
    return web.Response(
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Range",
        }
    )


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_route("OPTIONS", "/{path:.+}", handle_options)
    app.router.add_route("GET", "/{path:.+}", handle_media)
    return app


class MediaServer:
    """Background thread running the aiohttp file server."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8765):
        self.host = host
        self.port = port
        self.local_ip = get_local_ip()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None

    def url_for(self, file_path: str | Path) -> str:
        """Return the URL Chromecast should use to fetch a local file."""
        abs_path = str(Path(file_path).resolve())
        # Strip leading slash; the route pattern is /{path:.+}
        rel = abs_path.lstrip("/")
        return f"http://{self.local_ip}:{self.port}/{rel}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        app = make_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        # Run forever until loop is stopped
        await asyncio.Event().wait()

    def stop(self) -> None:
        if self._loop and self._runner:
            asyncio.run_coroutine_threadsafe(self._runner.cleanup(), self._loop)
