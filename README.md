# chromecast-tui

Terminal UI for discovering and casting media to Chromecast and Roku devices on the local network.
No browser required. No cloud. All traffic stays on your LAN.

## How it works

```
Your machine (192.168.x.y)                Chromecast / Roku (192.168.x.z)
──────────────────────────                ─────────────────────────
aiohttp file server :8765
exposes local files as HTTP
        │
        │  1. send URL via device protocol
        │  ──── TLS :8009 (Cast) / HTTP :8060 (Roku) ────────────►
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
- Your machine and the target device on the same LAN (same subnet)

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

When the app starts, it also exposes a local remote page:

```text
http://<your-lan-ip>:8765/remote
```

Use the `Connect Mobile` action (`c` key) to open a dedicated modal with URL
and QR code for iPhone access.

### Workflow

1. The app scans the network on startup (Chromecast + Roku)
2. Select a device from the left panel to connect
3. Browse local files in the right panel and press Enter to cast
4. Or type a remote URL in the bottom input field and press Enter
5. Use the device filter input (`all`, `cast`, `roku`, `airplay` or partial text)

### iPhone flow

1. Run `uv run chromecast-tui` on your machine
2. Connect a target device in the TUI (Chromecast/Roku)
3. Press `c` in the TUI, then scan the QR from the `Connect Mobile` modal
   (or open `http://<your-lan-ip>:8765/remote`)
4. Upload a media file or paste a media URL
5. The server relays it to the currently connected device

### Keyboard shortcuts

| Key        | Action              |
|------------|---------------------|
| `Space`    | Play / Pause        |
| `s`        | Stop                |
| `r`        | Re-scan network     |
| `c`        | Connect Mobile      |
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

Devices fetch media directly from your HTTP server, so the file must be in a
format the target device can decode natively. For unsupported formats you will
need to transcode first with ffmpeg.

## Architecture

```
chromecast_tui/
    app.py          # Textual TUI, layout and event wiring
    cast_manager.py # multi-backend wrapper (Chromecast + Roku)
    media_server.py # aiohttp HTTP server with range-request support
```

## Stack

| Layer          | Library              | Version  |
|----------------|----------------------|----------|
| Chromecast     | pychromecast         | 14.x     |
| Roku           | ECP (HTTP API)       | built-in |
| File server    | aiohttp              | 3.x      |
| Terminal UI    | Textual              | 8.x      |
| Package mgmt   | uv                   | -        |

## Notes

- Discovery requires being on the same subnet as the target device.
  If running inside Docker, use `--network host`.
- The local file server binds to `0.0.0.0:8765`. Make sure that port is not
  blocked by a firewall between your machine and the Chromecast.
- Roku support is currently focused on discovery, connect, play/pause/stop and
  URL/file launch. Exact seek and direct volume/mute controls are limited by
  Roku's standard API.
- AirPlay filter and backend slot are present in the app architecture; protocol
  implementation is the next phase.
- The iPhone remote page controls the device currently connected in the TUI.
- pychromecast may connect to Google's infrastructure once during device
  authentication. After that, all communication is local.
