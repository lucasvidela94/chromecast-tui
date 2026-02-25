"""
Tests for the aiohttp media server.

We spin up a real server on a random port and make real HTTP requests
so we verify the actual wire behavior (range parsing, status codes, headers).
"""

import asyncio
import os
import tempfile

import pytest
import pytest_asyncio
from aiohttp import ClientSession, FormData
from aiohttp.test_utils import TestServer, TestClient

from src.chromecast_tui.media_server import make_app


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def sample_file():
    """Write a known binary payload to a temp file and yield its path."""
    content = b"0123456789" * 100  # 1000 bytes, easy to reason about
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(content)
        path = f.name
    yield path, content
    os.unlink(path)


@pytest_asyncio.fixture
async def client(aiohttp_client):
    app = make_app()
    return await aiohttp_client(app)


# ──────────────────────────────────────────────────────────────────────────────
# Full-file GET
# ──────────────────────────────────────────────────────────────────────────────

async def test_full_get_returns_200(client, sample_file):
    path, content = sample_file
    rel = path.lstrip("/")
    resp = await client.get(f"/{rel}")
    assert resp.status == 200
    body = await resp.read()
    assert body == content


async def test_full_get_content_type_mp4(client, sample_file):
    path, _ = sample_file
    rel = path.lstrip("/")
    resp = await client.get(f"/{rel}")
    assert "video/mp4" in resp.headers["Content-Type"]


async def test_full_get_accept_ranges_header(client, sample_file):
    path, _ = sample_file
    rel = path.lstrip("/")
    resp = await client.get(f"/{rel}")
    assert resp.headers.get("Accept-Ranges") == "bytes"


async def test_full_get_cors_header(client, sample_file):
    path, _ = sample_file
    rel = path.lstrip("/")
    resp = await client.get(f"/{rel}")
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"


# ──────────────────────────────────────────────────────────────────────────────
# Range requests (the critical seeking path)
# ──────────────────────────────────────────────────────────────────────────────

async def test_range_returns_206(client, sample_file):
    path, _ = sample_file
    rel = path.lstrip("/")
    resp = await client.get(f"/{rel}", headers={"Range": "bytes=0-9"})
    assert resp.status == 206


async def test_range_returns_correct_slice(client, sample_file):
    path, content = sample_file
    rel = path.lstrip("/")
    resp = await client.get(f"/{rel}", headers={"Range": "bytes=10-19"})
    body = await resp.read()
    assert body == content[10:20]


async def test_range_content_range_header(client, sample_file):
    path, content = sample_file
    rel = path.lstrip("/")
    total = len(content)
    resp = await client.get(f"/{rel}", headers={"Range": "bytes=0-9"})
    assert resp.headers["Content-Range"] == f"bytes 0-9/{total}"


async def test_range_content_length_matches_slice(client, sample_file):
    path, _ = sample_file
    rel = path.lstrip("/")
    resp = await client.get(f"/{rel}", headers={"Range": "bytes=50-99"})
    assert resp.headers["Content-Length"] == "50"


async def test_range_open_ended(client, sample_file):
    """bytes=500- should return from byte 500 to EOF."""
    path, content = sample_file
    rel = path.lstrip("/")
    resp = await client.get(f"/{rel}", headers={"Range": "bytes=500-"})
    assert resp.status == 206
    body = await resp.read()
    assert body == content[500:]


async def test_range_last_byte(client, sample_file):
    """bytes=999-999 on a 1000-byte file returns the last byte."""
    path, content = sample_file
    rel = path.lstrip("/")
    resp = await client.get(f"/{rel}", headers={"Range": "bytes=999-999"})
    assert resp.status == 206
    body = await resp.read()
    assert body == content[999:1000]


async def test_range_clamped_to_file_size(client, sample_file):
    """An end byte beyond EOF should be clamped, not error."""
    path, content = sample_file
    rel = path.lstrip("/")
    resp = await client.get(f"/{rel}", headers={"Range": "bytes=990-9999"})
    assert resp.status == 206
    body = await resp.read()
    assert body == content[990:]


# ──────────────────────────────────────────────────────────────────────────────
# CORS preflight
# ──────────────────────────────────────────────────────────────────────────────

async def test_options_preflight(client, sample_file):
    path, _ = sample_file
    rel = path.lstrip("/")
    resp = await client.options(f"/{rel}")
    assert resp.status == 200
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    assert "GET" in resp.headers.get("Access-Control-Allow-Methods", "")


# ──────────────────────────────────────────────────────────────────────────────
# Error cases
# ──────────────────────────────────────────────────────────────────────────────

async def test_missing_file_returns_404(client):
    resp = await client.get("/nonexistent/path/file.mp4")
    assert resp.status == 404


async def test_directory_returns_404(client):
    resp = await client.get("/tmp")
    assert resp.status == 404


async def test_remote_page_returns_200(client):
    resp = await client.get("/remote")
    assert resp.status == 200
    body = await resp.text()
    assert "Chromecast TUI Remote" in body


async def test_cast_url_calls_callback(aiohttp_client):
    called = []

    def _cb(url: str, title: str) -> None:
        called.append((url, title))

    app = make_app(on_remote_cast=_cb)
    client = await aiohttp_client(app)
    resp = await client.post("/api/cast-url", json={"url": "https://example.com/test.mp4", "title": "X"})
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert called == [("https://example.com/test.mp4", "X")]


async def test_upload_cast_saves_file_and_calls_callback(aiohttp_client, tmp_path):
    called = []

    def _cb(url: str, title: str) -> None:
        called.append((url, title))

    app = make_app(on_remote_cast=_cb, upload_dir=tmp_path)
    client = await aiohttp_client(app)
    form = FormData()
    form.add_field("file", b"hello world", filename="clip.mp4", content_type="video/mp4")
    resp = await client.post("/api/upload-cast", data=form)
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert data["name"] == "clip.mp4"
    assert called and called[0][1] == "clip.mp4"
