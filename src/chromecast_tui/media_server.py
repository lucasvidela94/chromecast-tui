"""
Local HTTP file server with range-request support so Chromecast can seek.
Runs in a background thread using aiohttp.
"""

import asyncio
import json
import mimetypes
import os
import socket
import threading
import uuid
from pathlib import Path
from typing import Callable

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


def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Range",
    }


async def handle_remote_page(request: web.Request) -> web.Response:
    html = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Chromecast TUI Remote</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 16px; max-width: 680px; }
    h1 { font-size: 1.2rem; }
    .card { border: 1px solid #ddd; border-radius: 8px; padding: 12px; margin-bottom: 12px; }
    input, button { width: 100%; padding: 10px; margin-top: 8px; box-sizing: border-box; }
    button { cursor: pointer; }
    pre { white-space: pre-wrap; background: #f7f7f7; padding: 10px; border-radius: 6px; }
  </style>
</head>
<body>
  <h1>Chromecast TUI Remote</h1>
  <p>Conecta primero un dispositivo en la TUI y luego usa esta pagina.</p>
  <div class="card">
    <strong>Enviar URL</strong>
    <input id="url" placeholder="https://example.com/video.mp4" />
    <button id="send-url">Transmitir URL</button>
  </div>
  <div class="card">
    <strong>Subir archivo y transmitir</strong>
    <input id="file" type="file" />
    <button id="send-file">Subir y transmitir</button>
  </div>
  <pre id="out">Listo.</pre>
  <script>
    const out = document.getElementById("out");
    function log(v) { out.textContent = typeof v === "string" ? v : JSON.stringify(v, null, 2); }

    document.getElementById("send-url").addEventListener("click", async () => {
      const url = document.getElementById("url").value.trim();
      if (!url) { log("Falta URL"); return; }
      const res = await fetch("/api/cast-url", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({url})
      });
      log(await res.json());
    });

    document.getElementById("send-file").addEventListener("click", async () => {
      const fileInput = document.getElementById("file");
      if (!fileInput.files.length) { log("Selecciona un archivo"); return; }
      const fd = new FormData();
      fd.append("file", fileInput.files[0]);
      const res = await fetch("/api/upload-cast", { method: "POST", body: fd });
      log(await res.json());
    });
  </script>
</body>
</html>
"""
    return web.Response(text=html, content_type="text/html", headers=_cors_headers())


async def handle_cast_url(request: web.Request) -> web.Response:
    callback = request.app["on_remote_cast"]
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        payload = {}
    url = str(payload.get("url", "")).strip()
    title = str(payload.get("title", "")).strip()
    if not url:
        return web.json_response({"ok": False, "error": "missing url"}, status=400, headers=_cors_headers())
    try:
        callback(url, title)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500, headers=_cors_headers())
    return web.json_response({"ok": True, "url": url}, headers=_cors_headers())


async def handle_upload_cast(request: web.Request) -> web.Response:
    callback = request.app["on_remote_cast"]
    upload_dir = Path(request.app["upload_dir"])
    reader = await request.multipart()
    field = await reader.next()
    if field is None or field.name != "file":
        return web.json_response({"ok": False, "error": "missing file"}, status=400, headers=_cors_headers())
    filename = field.filename or "upload.bin"
    ext = Path(filename).suffix
    saved = upload_dir / f"{uuid.uuid4().hex}{ext}"
    with open(saved, "wb") as f:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            f.write(chunk)
    rel = str(saved.resolve()).lstrip("/")
    media_url = f"{request.scheme}://{request.host}/{rel}"
    try:
        callback(media_url, filename)
    except Exception as e:
        return web.json_response(
            {"ok": False, "error": str(e), "url": media_url},
            status=500,
            headers=_cors_headers(),
        )
    return web.json_response({"ok": True, "url": media_url, "name": filename}, headers=_cors_headers())


def make_app(
    on_remote_cast: Callable[[str, str], None] | None = None,
    upload_dir: str | Path | None = None,
) -> web.Application:
    app = web.Application()
    app["on_remote_cast"] = on_remote_cast or (lambda _url, _title: None)
    upload_path = Path(upload_dir or ".uploads").resolve()
    upload_path.mkdir(parents=True, exist_ok=True)
    app["upload_dir"] = str(upload_path)
    app.router.add_route("GET", "/remote", handle_remote_page)
    app.router.add_route("POST", "/api/cast-url", handle_cast_url)
    app.router.add_route("POST", "/api/upload-cast", handle_upload_cast)
    app.router.add_route("OPTIONS", "/{path:.+}", handle_options)
    app.router.add_route("GET", "/{path:.+}", handle_media)
    return app


class MediaServer:
    """Background thread running the aiohttp file server."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        on_remote_cast: Callable[[str, str], None] | None = None,
        upload_dir: str | Path | None = None,
    ):
        self.host = host
        self.port = port
        self.local_ip = get_local_ip()
        self._on_remote_cast = on_remote_cast
        self._upload_dir = upload_dir
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None

    def url_for(self, file_path: str | Path) -> str:
        """Return the URL Chromecast should use to fetch a local file."""
        abs_path = str(Path(file_path).resolve())
        # Strip leading slash; the route pattern is /{path:.+}
        rel = abs_path.lstrip("/")
        return f"http://{self.local_ip}:{self.port}/{rel}"

    def remote_url(self) -> str:
        return f"http://{self.local_ip}:{self.port}/remote"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        app = make_app(on_remote_cast=self._on_remote_cast, upload_dir=self._upload_dir)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        # Run forever until loop is stopped
        await asyncio.Event().wait()

    def stop(self) -> None:
        if self._loop and self._runner:
            asyncio.run_coroutine_threadsafe(self._runner.cleanup(), self._loop)
