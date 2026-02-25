"""
Chromecast discovery and control wrapper around pychromecast.
All blocking calls run in a thread pool so they don't freeze the TUI.
"""

import mimetypes
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pychromecast
from pychromecast import Chromecast
from pychromecast.controllers.media import MediaStatus, MediaStatusListener


SUPPORTED_EXTENSIONS = {
    # Video
    ".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v",
    # Audio
    ".mp3", ".flac", ".wav", ".ogg", ".opus", ".aac", ".m4a",
    # Image
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
}


@dataclass
class DeviceInfo:
    name: str
    host: str
    port: int
    model_name: str
    cast_type: str


@dataclass
class PlaybackState:
    status: str = "idle"          # idle | playing | paused | buffering
    title: str = ""
    content_id: str = ""
    current_time: float = 0.0
    duration: float = 0.0
    volume: float = 1.0
    is_muted: bool = False
    player_state: str = ""


class CastManager:
    """Manages a single active Chromecast connection."""

    def __init__(self, on_state_change: Callable[[PlaybackState], None] | None = None):
        self._cast: Chromecast | None = None
        self._browser = None
        self._lock = threading.Lock()
        self._on_state_change = on_state_change
        self.state = PlaybackState()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, timeout: float = 5.0) -> list[DeviceInfo]:
        """Scan the network and return found Chromecast devices."""
        chromecasts, browser = pychromecast.get_chromecasts(timeout=timeout)
        pychromecast.stop_discovery(browser)
        devices = []
        for cc in chromecasts:
            devices.append(DeviceInfo(
                name=cc.name,
                host=cc.host,
                port=cc.port,
                model_name=cc.model_name or "Chromecast",
                cast_type=cc.cast_type,
            ))
        return devices

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, device: DeviceInfo, timeout: float = 10.0) -> None:
        """Connect (and wait) to the given device."""
        self.disconnect()
        chromecasts, browser = pychromecast.get_listed_chromecasts(
            friendly_names=[device.name],
            timeout=timeout,
        )
        if not chromecasts:
            raise ConnectionError(f"Device '{device.name}' not found")

        cast = chromecasts[0]
        cast.wait(timeout=timeout)

        listener = _StatusListener(self._on_media_status)
        cast.media_controller.register_status_listener(listener)

        with self._lock:
            self._cast = cast
            self._browser = browser

        # Sync initial volume
        self.state.volume = cast.status.volume_level if cast.status else 1.0
        self.state.is_muted = cast.status.volume_muted if cast.status else False

    def disconnect(self) -> None:
        with self._lock:
            if self._cast:
                try:
                    self._cast.disconnect()
                except Exception:
                    pass
                self._cast = None
            if self._browser:
                try:
                    pychromecast.stop_discovery(self._browser)
                except Exception:
                    pass
                self._browser = None
        self.state = PlaybackState()

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._cast is not None

    @property
    def device_name(self) -> str:
        with self._lock:
            return self._cast.name if self._cast else ""

    # ------------------------------------------------------------------
    # Casting
    # ------------------------------------------------------------------

    def cast_url(self, url: str, content_type: str, title: str = "") -> None:
        """Cast a remote URL directly."""
        mc = self._media_controller()
        mc.play_media(url, content_type, title=title or url)

    def cast_file(self, file_path: str | Path, server_url: str, title: str = "") -> None:
        """
        Cast a local file.  `server_url` is the full http://LAN_IP:port/... URL
        that the MediaServer is exposing for this file.
        """
        path = Path(file_path)
        mime, _ = mimetypes.guess_type(str(path))
        if not mime:
            mime = "application/octet-stream"
        mc = self._media_controller()
        mc.play_media(server_url, mime, title=title or path.name)

    # ------------------------------------------------------------------
    # Playback controls
    # ------------------------------------------------------------------

    def play(self) -> None:
        self._media_controller().play()

    def pause(self) -> None:
        self._media_controller().pause()

    def stop(self) -> None:
        self._media_controller().stop()

    def seek(self, seconds: float) -> None:
        self._media_controller().seek(seconds)

    def set_volume(self, level: float) -> None:
        """level: 0.0 – 1.0"""
        level = max(0.0, min(1.0, level))
        with self._lock:
            if self._cast:
                self._cast.set_volume(level)
        self.state.volume = level

    def toggle_mute(self) -> None:
        with self._lock:
            if self._cast:
                self._cast.set_volume_muted(not self.state.is_muted)

    def quit_app(self) -> None:
        with self._lock:
            if self._cast:
                self._cast.quit_app()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _media_controller(self):
        with self._lock:
            if not self._cast:
                raise RuntimeError("Not connected to any Chromecast")
            return self._cast.media_controller

    def _on_media_status(self, status: MediaStatus) -> None:
        """Called by pychromecast when media state changes."""
        player_state = status.player_state or ""
        self.state.player_state = player_state
        self.state.title = status.title or ""
        self.state.content_id = status.content_id or ""
        self.state.current_time = status.current_time or 0.0
        self.state.duration = status.duration or 0.0

        mapping = {
            "PLAYING": "playing",
            "PAUSED": "paused",
            "BUFFERING": "buffering",
            "IDLE": "idle",
        }
        self.state.status = mapping.get(player_state.upper(), "idle")

        if self._on_state_change:
            self._on_state_change(self.state)


class _StatusListener(MediaStatusListener):
    def __init__(self, callback: Callable[[MediaStatus], None]):
        self._callback = callback

    def new_media_status(self, status: MediaStatus) -> None:
        self._callback(status)

    def load_media_failed(self, item, error_code) -> None:
        pass
