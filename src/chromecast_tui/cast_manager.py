"""
Chromecast discovery and control wrapper around pychromecast.
All blocking calls run in a thread pool so they don't freeze the TUI.
"""

import mimetypes
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

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
    backend: str = "chromecast"


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
        self._active_backend = "chromecast"
        self._roku_device: DeviceInfo | None = None
        self._airplay_device: DeviceInfo | None = None

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, timeout: float = 5.0) -> list[DeviceInfo]:
        """Scan the network and return found Chromecast devices."""
        devices: list[DeviceInfo] = []
        chromecasts, browser = pychromecast.get_chromecasts(timeout=timeout)
        pychromecast.stop_discovery(browser)
        for cc in chromecasts:
            cast_info = getattr(cc, "cast_info", None)
            devices.append(DeviceInfo(
                name=(
                    getattr(cc, "name", None)
                    or getattr(cast_info, "friendly_name", None)
                    or "Unknown Chromecast"
                ),
                host=(
                    getattr(cc, "host", None)
                    or getattr(cast_info, "host", None)
                    or ""
                ),
                port=(
                    getattr(cc, "port", None)
                    or getattr(cast_info, "port", None)
                    or 8009
                ),
                model_name=(
                    getattr(cc, "model_name", None)
                    or getattr(cast_info, "model_name", None)
                    or "Chromecast"
                ),
                cast_type=(
                    getattr(cc, "cast_type", None)
                    or getattr(cast_info, "cast_type", None)
                    or "cast"
                ),
                backend="chromecast",
            ))
        try:
            devices.extend(self._discover_roku(timeout=min(2.0, timeout)))
        except Exception:
            pass
        try:
            devices.extend(self._discover_airplay(timeout=min(2.0, timeout)))
        except Exception:
            pass
        return devices

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, device: DeviceInfo, timeout: float = 10.0) -> None:
        """Connect (and wait) to the given device."""
        self.disconnect()
        if device.backend == "roku":
            self._connect_roku(device, timeout=timeout)
            return
        if device.backend == "airplay":
            self._connect_airplay(device, timeout=timeout)
            return
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
            self._active_backend = "chromecast"
            self._roku_device = None
            self._airplay_device = None

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
            self._roku_device = None
            self._airplay_device = None
            self._active_backend = "chromecast"
        self.state = PlaybackState()

    @property
    def connected(self) -> bool:
        with self._lock:
            return (
                self._cast is not None
                or self._roku_device is not None
                or self._airplay_device is not None
            )

    @property
    def device_name(self) -> str:
        with self._lock:
            if self._cast:
                return self._cast.name
            if self._roku_device:
                return self._roku_device.name
            if self._airplay_device:
                return self._airplay_device.name
            return ""

    # ------------------------------------------------------------------
    # Casting
    # ------------------------------------------------------------------

    def cast_url(self, url: str, content_type: str, title: str = "") -> None:
        """Cast a remote URL directly."""
        if self._active_backend == "roku":
            self._roku_cast_url(url, content_type)
            return
        if self._active_backend == "airplay":
            raise RuntimeError("AirPlay backend pendiente de implementacion")
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
        if self._active_backend == "roku":
            self._roku_cast_url(server_url, mime)
            return
        if self._active_backend == "airplay":
            raise RuntimeError("AirPlay backend pendiente de implementacion")
        mc = self._media_controller()
        mc.play_media(server_url, mime, title=title or path.name)

    # ------------------------------------------------------------------
    # Playback controls
    # ------------------------------------------------------------------

    def play(self) -> None:
        if self._active_backend == "roku":
            self._roku_keypress("Play")
            return
        if self._active_backend == "airplay":
            raise RuntimeError("AirPlay backend pendiente de implementacion")
        self._media_controller().play()

    def pause(self) -> None:
        if self._active_backend == "roku":
            self._roku_keypress("Play")
            return
        if self._active_backend == "airplay":
            raise RuntimeError("AirPlay backend pendiente de implementacion")
        self._media_controller().pause()

    def stop(self) -> None:
        if self._active_backend == "roku":
            self._roku_keypress("Home")
            return
        if self._active_backend == "airplay":
            raise RuntimeError("AirPlay backend pendiente de implementacion")
        self._media_controller().stop()

    def seek(self, seconds: float) -> None:
        if self._active_backend == "roku":
            raise RuntimeError("Roku no soporta seek absoluto")
        if self._active_backend == "airplay":
            raise RuntimeError("AirPlay backend pendiente de implementacion")
        self._media_controller().seek(seconds)

    def set_volume(self, level: float) -> None:
        """level: 0.0 – 1.0"""
        level = max(0.0, min(1.0, level))
        if self._active_backend == "roku":
            raise RuntimeError("Roku no soporta volumen por API estándar")
        if self._active_backend == "airplay":
            raise RuntimeError("AirPlay backend pendiente de implementacion")
        with self._lock:
            if self._cast:
                self._cast.set_volume(level)
        self.state.volume = level

    def toggle_mute(self) -> None:
        if self._active_backend == "roku":
            raise RuntimeError("Roku no soporta mute por API estándar")
        if self._active_backend == "airplay":
            raise RuntimeError("AirPlay backend pendiente de implementacion")
        with self._lock:
            if self._cast:
                self._cast.set_volume_muted(not self.state.is_muted)

    def quit_app(self) -> None:
        if self._active_backend == "roku":
            self._roku_keypress("Home")
            return
        if self._active_backend == "airplay":
            raise RuntimeError("AirPlay backend pendiente de implementacion")
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

    def _connect_roku(self, device: DeviceInfo, timeout: float = 10.0) -> None:
        url = f"http://{device.host}:{device.port}/query/device-info"
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout):
            pass
        with self._lock:
            self._roku_device = device
            self._active_backend = "roku"
            self._cast = None
            self._browser = None
            self._airplay_device = None

    def _connect_airplay(self, device: DeviceInfo, timeout: float = 10.0) -> None:
        raise RuntimeError("AirPlay backend pendiente de implementacion")

    def _discover_roku(self, timeout: float = 3.0) -> list[DeviceInfo]:
        msg = (
            "M-SEARCH * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            "MAN: \"ssdp:discover\"\r\n"
            "ST: roku:ecp\r\n"
            "MX: 2\r\n\r\n"
        ).encode("ascii")

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.settimeout(0.4)
        sock.sendto(msg, ("239.255.255.250", 1900))

        devices: list[DeviceInfo] = []
        seen: set[str] = set()
        import time
        deadline = time.time() + max(1.0, timeout)
        try:
            while time.time() < deadline:
                try:
                    data, _ = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                headers = _parse_ssdp_headers(data.decode("utf-8", errors="ignore"))
                location = headers.get("location", "")
                if not location:
                    continue
                host = _host_from_url(location)
                if not host or host in seen:
                    continue
                info = self._roku_device_info(host, timeout=1.5)
                if info is None:
                    continue
                seen.add(host)
                devices.append(info)
        finally:
            sock.close()
        return devices

    def _discover_airplay(self, timeout: float = 3.0) -> list[DeviceInfo]:
        return []

    def _roku_device_info(self, host: str, timeout: float = 2.0) -> DeviceInfo | None:
        url = f"http://{host}:8060/query/device-info"
        req = Request(url, method="GET")
        try:
            with urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
        except Exception:
            return None
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            return None
        name = root.findtext("user-device-name") or root.findtext("friendly-device-name") or f"Roku ({host})"
        model = root.findtext("model-name") or "Roku"
        return DeviceInfo(
            name=name,
            host=host,
            port=8060,
            model_name=model,
            cast_type="roku",
            backend="roku",
        )

    def _roku_keypress(self, key: str, timeout: float = 3.0) -> None:
        with self._lock:
            device = self._roku_device
        if not device:
            raise RuntimeError("Not connected to any Roku device")
        req = Request(f"http://{device.host}:{device.port}/keypress/{key}", method="POST")
        with urlopen(req, timeout=timeout):
            pass

    def _roku_cast_url(self, url: str, content_type: str, timeout: float = 5.0) -> None:
        with self._lock:
            device = self._roku_device
        if not device:
            raise RuntimeError("Not connected to any Roku device")
        media_type = "v"
        if content_type.startswith("audio/"):
            media_type = "a"
        elif content_type.startswith("image/"):
            media_type = "p"
        encoded = quote_plus(url)
        req = Request(
            f"http://{device.host}:{device.port}/input/15985?t={media_type}&u={encoded}",
            method="POST",
        )
        with urlopen(req, timeout=timeout):
            pass

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


def _parse_ssdp_headers(payload: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    lines = payload.splitlines()
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def _host_from_url(url: str) -> str:
    if "//" not in url:
        return ""
    host_port = url.split("//", 1)[1].split("/", 1)[0]
    if ":" in host_port:
        return host_port.split(":", 1)[0]
    return host_port
