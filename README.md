# chromecast-tui

Terminal UI for discovering and casting media to Chromecast devices on the local network.
No browser required. No cloud. All traffic stays on your LAN.

## How it works

```
Your machine (192.168.x.y)                Chromecast (192.168.x.z)
──────────────────────────                ─────────────────────────
aiohttp file server :8765
exposes local files as HTTP
        │
        │  1. send URL via Cast protocol
        │  ──── TLS :8009 ────────────►
        │                                  2. fetches file from your machine
        │  ◄──── HTTP :8765 ─────────────
        │  serves chunks (range requests)
```

The Chromecast pulls media directly from your machine over HTTP.
Files are never copied or uploaded anywhere.
Seeking works because the server speaks HTTP range requests (206 Partial Content).

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Your machine and the Chromecast on the same LAN (same subnet)

## Installation

```bash
git clone <repo>
cd chromecast-tui
uv sync
```

## Usage

```bash
uv run chromecast-tui
```

### Workflow

1. The app scans the network on startup (mDNS on `_googlecast._tcp.local`)
2. Select a device from the left panel to connect
3. Browse local files in the right panel and press Enter to cast
4. Or type a remote URL in the bottom input field and press Enter

### Keyboard shortcuts

| Key        | Action              |
|------------|---------------------|
| `Space`    | Play / Pause        |
| `s`        | Stop                |
| `r`        | Re-scan network     |
| `m`        | Toggle mute         |
| `Left`     | Seek back 10s       |
| `Right`    | Seek forward 10s    |
| `Up`       | Volume +5%          |
| `Down`     | Volume -5%          |
| `q`        | Quit                |

## Supported formats

| Type   | Extensions                                      |
|--------|-------------------------------------------------|
| Video  | `.mp4`, `.webm`, `.mkv`, `.avi`, `.mov`, `.m4v` |
| Audio  | `.mp3`, `.flac`, `.wav`, `.ogg`, `.opus`, `.aac`, `.m4a` |
| Image  | `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.bmp` |

Chromecast fetches directly from your HTTP server, so the file must be in a
format the device can decode natively. For unsupported formats (e.g. MKV with
AC3 audio) you will need to transcode first with ffmpeg.

## Architecture

```
chromecast_tui/
    app.py          # Textual TUI, layout and event wiring
    cast_manager.py # pychromecast wrapper (discovery, connection, controls)
    media_server.py # aiohttp HTTP server with range-request support
```

## Stack

| Layer          | Library              | Version  |
|----------------|----------------------|----------|
| Cast protocol  | pychromecast         | 14.x     |
| File server    | aiohttp              | 3.x      |
| Terminal UI    | Textual              | 8.x      |
| Package mgmt   | uv                   | -        |

## Notes

- mDNS discovery requires being on the same subnet as the Chromecast.
  If running inside Docker, use `--network host`.
- The local file server binds to `0.0.0.0:8765`. Make sure that port is not
  blocked by a firewall between your machine and the Chromecast.
- pychromecast connects to Google's infrastructure once during device
  authentication. After that, all communication is local.
