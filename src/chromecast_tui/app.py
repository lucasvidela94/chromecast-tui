"""
Chromecast TUI — Textual application.

Layout:
┌─────────────────────────────────────────────────────┐
│  Header: device name + status                       │
├──────────────────────┬──────────────────────────────┤
│  Device list         │  File browser                │
│  (left panel)        │  (right panel)               │
├──────────────────────┴──────────────────────────────┤
│  Now playing bar                                    │
├─────────────────────────────────────────────────────┤
│  Controls: ◀◀  ▶/⏸  ■  ▶▶  🔊▁▁▁▁  [URL input]  │
└─────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import threading
from pathlib import Path
import mimetypes

import qrcode
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    Static,
)

from .cast_manager import CastManager, DeviceInfo, PlaybackState, SUPPORTED_EXTENSIONS
from .media_server import MediaServer


# ──────────────────────────────────────────────────────────────────────────────
# Small helper widgets
# ──────────────────────────────────────────────────────────────────────────────

class NowPlayingBar(Static):
    DEFAULT_CSS = """
    NowPlayingBar {
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
        text-overflow: ellipsis;
    }
    """

    def update_state(self, state: PlaybackState) -> None:
        icon = {"playing": "▶", "paused": "⏸", "buffering": "⟳", "idle": "■"}.get(
            state.status, "■"
        )
        if state.title:
            dur = _fmt_time(state.duration)
            cur = _fmt_time(state.current_time)
            self.update(f" {icon}  {state.title}  [{cur} / {dur}]")
        else:
            self.update(f" {icon}  —")


class VolumeBar(Horizontal):
    DEFAULT_CSS = """
    VolumeBar {
        height: 3;
        width: 20;
        align: left middle;
    }
    VolumeBar Label {
        width: 3;
        content-align: center middle;
    }
    VolumeBar Input {
        width: 8;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("VOL")
        yield Input(value="80", id="vol-input", placeholder="0-100")


class MobileConnectScreen(ModalScreen[None]):
    CSS = """
    MobileConnectScreen {
        align: center middle;
    }
    #mobile-connect {
        width: 72;
        height: auto;
        max-height: 90%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #mobile-title {
        content-align: center middle;
        width: 100%;
        margin-bottom: 1;
    }
    #mobile-url {
        margin-bottom: 1;
        color: $text-muted;
    }
    #mobile-qr {
        width: 100%;
        content-align: center middle;
        height: auto;
        margin-bottom: 1;
    }
    #mobile-close {
        width: 100%;
    }
    """

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, remote_url: str):
        super().__init__()
        self._remote_url = remote_url

    def compose(self) -> ComposeResult:
        with Vertical(id="mobile-connect"):
            yield Label("Connect Mobile", id="mobile-title")
            yield Label(f"Open in iPhone: {self._remote_url}", id="mobile-url")
            yield Static(_ascii_qr(self._remote_url), id="mobile-qr")
            yield Button("Close", id="mobile-close", variant="primary")

    @on(Button.Pressed, "#mobile-close")
    def on_close_pressed(self) -> None:
        self.dismiss()


# ──────────────────────────────────────────────────────────────────────────────
# Main App
# ──────────────────────────────────────────────────────────────────────────────

class ChromecastApp(App):
    """Terminal UI for casting media to Chromecast."""

    TITLE = "Chromecast TUI"
    CSS = """
    Screen {
        layout: vertical;
    }

    /* Top panels */
    #panels {
        height: 1fr;
        layout: horizontal;
    }

    #left-panel {
        width: 35;
        border: solid $panel-lighten-1;
        padding: 0 1;
    }

    #right-panel {
        width: 1fr;
        border: solid $panel-lighten-1;
        padding: 0 1;
    }

    #left-panel Label, #right-panel Label {
        background: $panel;
        color: $text;
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }

    /* Device table */
    #device-table {
        height: 1fr;
    }

    /* Now playing */
    NowPlayingBar {
        height: 1;
    }

    /* Controls row */
    #controls {
        height: 5;
        background: $panel;
        padding: 1 2;
        align: left middle;
        layout: horizontal;
    }

    #controls Button {
        margin: 0 1;
        min-width: 5;
    }

    VolumeBar {
        margin-left: 2;
    }

    #url-input {
        margin-left: 2;
        width: 1fr;
    }

    /* Status footer label */
    #status-label {
        height: 1;
        background: $panel-darken-1;
        color: $text;
        padding: 0 1;
        display: none;
    }
    #status-label.visible {
        display: block;
    }
    #seek-preview-label {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }

    /* Scan button */
    #btn-scan {
        margin-top: 1;
        width: 100%;
    }
    #btn-mobile {
        margin-top: 1;
        width: 100%;
    }
    #filter-query {
        margin-bottom: 1;
        width: 100%;
    }

    /* Directory tree */
    DirectoryTree {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("space", "toggle_play", "Play/Pause"),
        Binding("s", "stop", "Stop"),
        Binding("q", "quit", "Quit"),
        Binding("r", "scan", "Scan"),
        Binding("c", "open_mobile", "Connect Mobile"),
        Binding("m", "toggle_mute", "Mute"),
        Binding("left", "seek_back", "« 10s"),
        Binding("right", "seek_fwd", "10s »"),
        Binding("]", "seek_fwd_30", "+30s"),
        Binding("}", "seek_fwd_100", "+100s"),
        Binding("up", "vol_up", "Vol +"),
        Binding("down", "vol_down", "Vol -"),
    ]

    # Reactives drive UI updates from the worker thread
    _state: reactive[PlaybackState] = reactive(PlaybackState, recompose=False)
    _status_msg: reactive[str] = reactive("")

    def __init__(self):
        super().__init__()
        self._server = MediaServer(on_remote_cast=self._on_remote_cast)
        self._cast = CastManager(on_state_change=self._on_cast_state)
        self._devices: list[DeviceInfo] = []
        self._visible_devices: list[DeviceInfo] = []
        self._selected_device: DeviceInfo | None = None
        self._filter_query: str = ""
        self._control_lock = threading.Lock()
        self._server.start()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal(id="panels"):
            # Left: device list
            with Vertical(id="left-panel"):
                yield Label("[ Devices ]")
                yield Input(placeholder="Filter devices: all/cast/roku/airplay", id="filter-query")
                yield DataTable(id="device-table", cursor_type="row", zebra_stripes=True)
                yield Button("Scan", id="btn-scan", variant="default")
                yield Button("Connect Mobile", id="btn-mobile", variant="default")

            # Right: file browser
            with Vertical(id="right-panel"):
                yield Label("[ Files ]")
                yield DirectoryTree(str(Path.home()), id="file-tree")

        yield NowPlayingBar(id="now-playing", markup=False)
        yield ProgressBar(total=100, id="seek-bar", show_eta=False, show_percentage=False)
        yield Label("", id="seek-preview-label")

        # Controls
        with Horizontal(id="controls"):
            yield Button("«", id="btn-rew",  variant="default")
            yield Button("▶", id="btn-play", variant="default")
            yield Button("■", id="btn-stop", variant="default")
            yield Button("»", id="btn-ffw",  variant="default")
            yield Button("+30s", id="btn-ffw-30", variant="default")
            yield Button("+100s", id="btn-ffw-100", variant="default")
            yield VolumeBar()
            yield Input(placeholder="Seek: +30, -10, 1:23", id="seek-input")
            yield Button("Go", id="btn-seek-go", variant="default")
            yield Input(placeholder="Cast URL directly…", id="url-input")

        yield Label(id="status-label")
        yield Footer()

    def on_mount(self) -> None:
        # Set up device table columns
        table = self.query_one("#device-table", DataTable)
        table.add_columns("Name", "Model", "Type", "Host")
        # Auto-scan on start
        self.action_scan()
        self._set_status("Press 'c' for Connect Mobile", clear_after=5)

    # ------------------------------------------------------------------
    # Device scanning
    # ------------------------------------------------------------------

    @work(thread=True)
    def action_scan(self) -> None:
        self._set_status("Scanning network…")
        try:
            devices = self._cast.discover(timeout=5.0)
            self.call_from_thread(self._populate_devices, devices)
            msg = f"Found {len(devices)} device(s)" if devices else "No devices found"
            self._set_status(msg, clear_after=3)
        except Exception as e:
            self._set_status(f"Scan error: {e}", clear_after=5)

    def _populate_devices(self, devices: list[DeviceInfo]) -> None:
        self._devices = devices
        self._refresh_visible_devices()

    def _refresh_visible_devices(self) -> None:
        query = self._filter_query.strip().lower()
        if not query or query == "all":
            filtered = self._devices
        else:
            filtered = [
                d for d in self._devices
                if query in d.backend.lower()
                or query in d.name.lower()
                or query in d.model_name.lower()
                or query in d.host.lower()
            ]
        self._visible_devices = filtered
        table = self.query_one("#device-table", DataTable)
        table.clear()
        for d in filtered:
            table.add_row(d.name, d.model_name, d.backend, d.host)

    # ------------------------------------------------------------------
    # Device selection → connect
    # ------------------------------------------------------------------

    @on(DataTable.RowSelected, "#device-table")
    def on_device_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if idx < len(self._visible_devices):
            self._selected_device = self._visible_devices[idx]
            self._connect_to(self._selected_device)

    @on(Input.Changed, "#filter-query")
    def on_filter_query_changed(self, event: Input.Changed) -> None:
        self._filter_query = event.value
        self._refresh_visible_devices()

    @work(thread=True)
    def _connect_to(self, device: DeviceInfo) -> None:
        self._set_status(f"Connecting to {device.name}…")
        try:
            self._cast.connect(device)
            self._set_status(f"Connected ✓ {device.name}", clear_after=3)
            self.call_from_thread(self._update_title, device.name)
        except Exception as e:
            self._set_status(f"Connection failed: {e}", clear_after=6)

    def _update_title(self, name: str) -> None:
        self.title = f"Chromecast TUI — {name}"

    # ------------------------------------------------------------------
    # File browser → cast local file
    # ------------------------------------------------------------------

    @on(DirectoryTree.FileSelected, "#file-tree")
    def on_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        path = event.path
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            self._set_status(f"Unsupported format: {path.suffix}", clear_after=4)
            return
        if not self._cast.connected:
            self._set_status("Not connected to any device", clear_after=3)
            return
        self._cast_local_file(path)

    @work(thread=True)
    def _cast_local_file(self, path: Path) -> None:
        url = self._server.url_for(path)
        self._set_status(f"Casting {path.name}…")
        try:
            self._cast.cast_file(path, server_url=url)
        except Exception as e:
            self._set_status(f"Cast error: {e}", clear_after=5)

    # ------------------------------------------------------------------
    # URL input → cast remote URL
    # ------------------------------------------------------------------

    @on(Input.Submitted, "#url-input")
    def on_url_submitted(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if not url:
            return
        if not self._cast.connected:
            self._set_status("Not connected to any device", clear_after=3)
            return
        self._cast_remote_url(url)

    @work(thread=True)
    def _cast_remote_url(self, url: str) -> None:
        # Guess content type from URL
        import mimetypes
        mime, _ = mimetypes.guess_type(url)
        if not mime:
            mime = "video/mp4"
        self._set_status(f"Casting URL…")
        try:
            self._cast.cast_url(url, mime)
            self.call_from_thread(self.query_one("#url-input", Input).clear)
        except Exception as e:
            self._set_status(f"Cast error: {e}", clear_after=5)

    def _on_remote_cast(self, url: str, title: str = "") -> None:
        if not self._cast.connected:
            raise RuntimeError("No device connected in TUI")
        mime, _ = mimetypes.guess_type(url)
        if not mime:
            mime = "video/mp4"
        try:
            self._cast.cast_url(url, mime, title=title)
            shown = title or url
            self._set_status(f"Remote casting: {shown}", clear_after=4)
        except Exception as e:
            self._set_status(f"Remote cast error: {e}", clear_after=5)
            raise

    # ------------------------------------------------------------------
    # Volume input
    # ------------------------------------------------------------------

    @on(Input.Submitted, "#vol-input")
    def on_volume_submitted(self, event: Input.Submitted) -> None:
        try:
            level = int(event.value) / 100.0
            if self._cast.connected:
                self._cast.set_volume(level)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Transport buttons
    # ------------------------------------------------------------------

    @on(Button.Pressed, "#btn-play")
    def on_btn_play(self) -> None:
        self.action_toggle_play()

    @on(Button.Pressed, "#btn-stop")
    def on_btn_stop(self) -> None:
        self.action_stop()

    @on(Button.Pressed, "#btn-rew")
    def on_btn_rew(self) -> None:
        self.action_seek_back()

    @on(Button.Pressed, "#btn-ffw")
    def on_btn_ffw(self) -> None:
        self.action_seek_fwd()

    @on(Button.Pressed, "#btn-ffw-30")
    def on_btn_ffw_30(self) -> None:
        self.action_seek_fwd_30()

    @on(Button.Pressed, "#btn-ffw-100")
    def on_btn_ffw_100(self) -> None:
        self.action_seek_fwd_100()

    @on(Button.Pressed, "#btn-scan")
    def on_btn_scan(self) -> None:
        self.action_scan()

    @on(Button.Pressed, "#btn-mobile")
    def on_btn_mobile(self) -> None:
        self.action_open_mobile()

    @on(Button.Pressed, "#btn-seek-go")
    def on_btn_seek_go(self) -> None:
        self._submit_seek_input()

    @on(Input.Submitted, "#seek-input")
    def on_seek_input_submitted(self, event: Input.Submitted) -> None:
        self._submit_seek_input(event.value)

    @on(events.Click, "#seek-bar")
    def on_seek_bar_click(self, event: events.Click) -> None:
        if not self._cast.connected:
            return
        duration = self._cast.state.duration or 0.0
        if duration <= 0:
            return
        target = self._seek_target_from_bar_x(event.x)
        if target is None:
            return
        self._run_control_action("seek", target)

    @on(events.MouseMove, "#seek-bar")
    def on_seek_bar_move(self, event: events.MouseMove) -> None:
        if not self._cast.connected:
            return
        duration = self._cast.state.duration or 0.0
        if duration <= 0:
            return
        target = self._seek_target_from_bar_x(event.x)
        if target is None:
            return
        self.query_one("#seek-preview-label", Label).update(
            f" Seek preview: {_fmt_time(target)} / {_fmt_time(duration)}"
        )

    @on(events.Leave, "#seek-bar")
    def on_seek_bar_leave(self) -> None:
        self.query_one("#seek-preview-label", Label).update("")

    def _submit_seek_input(self, value: str | None = None) -> None:
        if not self._cast.connected:
            self._set_status("Not connected to any device", clear_after=3)
            return
        seek_input = self.query_one("#seek-input", Input)
        raw = (value if value is not None else seek_input.value).strip()
        if not raw:
            return
        target = _parse_seek_target(raw, self._cast.state.current_time, self._cast.state.duration)
        if target is None:
            self._set_status("Invalid seek format", clear_after=3)
            return
        self._run_control_action("seek", target)
        self.call_from_thread(seek_input.clear)

    def _seek_target_from_bar_x(self, x: int) -> float | None:
        duration = self._cast.state.duration or 0.0
        if duration <= 0:
            return None
        bar = self.query_one("#seek-bar", ProgressBar)
        width = max(1, bar.size.width)
        pos = max(0, min(x, width - 1))
        ratio = pos / max(1, width - 1)
        return duration * ratio

    # ------------------------------------------------------------------
    # Keybinding actions
    # ------------------------------------------------------------------

    def action_toggle_play(self) -> None:
        if self._cast.connected:
            if self._cast.state.status == "playing":
                self._run_control_action("pause")
            else:
                self._run_control_action("play")

    def action_stop(self) -> None:
        if self._cast.connected:
            self._run_control_action("stop")

    def action_toggle_mute(self) -> None:
        if self._cast.connected:
            self._run_control_action("toggle_mute")

    def action_seek_back(self) -> None:
        if self._cast.connected:
            self._seek_relative(-10)

    def action_seek_fwd(self) -> None:
        if self._cast.connected:
            self._seek_relative(10)

    def action_seek_fwd_30(self) -> None:
        if self._cast.connected:
            self._seek_relative(30)

    def action_seek_fwd_100(self) -> None:
        if self._cast.connected:
            self._seek_relative(100)

    def action_open_mobile(self) -> None:
        self.push_screen(MobileConnectScreen(self._server.remote_url()))

    def _seek_relative(self, delta_seconds: float) -> None:
        target = max(0.0, self._cast.state.current_time + delta_seconds)
        self._run_control_action("seek", target)

    def action_vol_up(self) -> None:
        if self._cast.connected:
            target = min(1.0, self._cast.state.volume + 0.05)
            self._run_control_action("set_volume", target)
            self._sync_vol_input()

    def action_vol_down(self) -> None:
        if self._cast.connected:
            target = max(0.0, self._cast.state.volume - 0.05)
            self._run_control_action("set_volume", target)
            self._sync_vol_input()

    @work(thread=True)
    def _run_control_action(self, action: str, value: float | None = None) -> None:
        with self._control_lock:
            try:
                self._execute_control_action(action, value)
                return
            except Exception as e:
                if self._is_connection_error(e) and self._attempt_reconnect():
                    try:
                        self._execute_control_action(action, value)
                        return
                    except Exception as retry_error:
                        self._set_status(f"Control error: {retry_error}", clear_after=4)
                        return
                self._set_status(f"Control error: {e}", clear_after=4)

    def _execute_control_action(self, action: str, value: float | None = None) -> None:
        if action == "play":
            self._cast.play()
        elif action == "pause":
            self._cast.pause()
        elif action == "stop":
            self._cast.stop()
        elif action == "toggle_mute":
            self._cast.toggle_mute()
        elif action == "seek":
            if value is None:
                raise ValueError("Missing seek target")
            self._cast.seek(value)
        elif action == "set_volume":
            if value is None:
                raise ValueError("Missing volume value")
            self._cast.set_volume(value)

    def _attempt_reconnect(self) -> bool:
        if not self._selected_device:
            return False
        device = self._selected_device
        self._set_status(f"Reconnecting to {device.name}…")
        try:
            self._cast.connect(device)
            self._set_status(f"Reconnected ✓ {device.name}", clear_after=2)
            return True
        except Exception as e:
            self._set_status(f"Reconnect failed: {e}", clear_after=4)
            return False

    def _is_connection_error(self, error: Exception) -> bool:
        text = str(error).lower()
        markers = (
            "not connected",
            "connection",
            "timeout",
            "socket",
            "broken pipe",
            "reset by peer",
            "transport",
        )
        return any(m in text for m in markers)

    def _sync_vol_input(self) -> None:
        pct = int(self._cast.state.volume * 100)
        self.query_one("#vol-input", Input).value = str(pct)

    # ------------------------------------------------------------------
    # State callbacks (from background thread)
    # ------------------------------------------------------------------

    def _on_cast_state(self, state: PlaybackState) -> None:
        """pychromecast calls this from its own thread → marshal to UI thread."""
        self.call_from_thread(self._apply_state, state)

    def _apply_state(self, state: PlaybackState) -> None:
        self.query_one(NowPlayingBar).update_state(state)
        bar = self.query_one("#seek-bar", ProgressBar)
        total = state.duration if state.duration > 0 else 100.0
        progress = min(state.current_time, state.duration) if state.duration > 0 else 0.0
        bar.update(progress=progress, total=total)
        # Update play button label
        btn = self.query_one("#btn-play", Button)
        btn.label = "⏸" if state.status == "playing" else "▶"

    # ------------------------------------------------------------------
    # Status messages
    # ------------------------------------------------------------------

    def _set_status(self, msg: str, clear_after: float = 0) -> None:
        if threading.current_thread() is threading.main_thread():
            self._show_status(msg)
        else:
            self.call_from_thread(self._show_status, msg)
        if clear_after:
            def _clear():
                import time
                time.sleep(clear_after)
                self.call_from_thread(self._show_status, "")
            threading.Thread(target=_clear, daemon=True).start()

    def _show_status(self, msg: str) -> None:
        label = self.query_one("#status-label", Label)
        if msg:
            label.update(f" {msg}")
            label.add_class("visible")
        else:
            label.update("")
            label.remove_class("visible")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def on_unmount(self) -> None:
        if self._cast.connected:
            try:
                self._cast.stop()
            except Exception:
                pass
        self._cast.disconnect()
        self._server.stop()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fmt_time(seconds: float) -> str:
    if not seconds:
        return "0:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _parse_seek_target(raw: str, current_time: float, duration: float) -> float | None:
    text = raw.strip()
    if not text:
        return None

    target: float | None = None
    if text[0] in {"+", "-"}:
        try:
            delta = float(text)
        except ValueError:
            return None
        target = current_time + delta
    elif ":" in text:
        parts = text.split(":")
        if len(parts) == 2:
            try:
                m = int(parts[0])
                s = int(parts[1])
            except ValueError:
                return None
            target = float(m * 60 + s)
        elif len(parts) == 3:
            try:
                h = int(parts[0])
                m = int(parts[1])
                s = int(parts[2])
            except ValueError:
                return None
            target = float(h * 3600 + m * 60 + s)
        else:
            return None
    else:
        try:
            target = float(text)
        except ValueError:
            return None

    target = max(0.0, target)
    if duration and duration > 0:
        target = min(target, duration)
    return target


def _ascii_qr(text: str) -> str:
    qr = qrcode.QRCode(border=4)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    lines = []
    for row in matrix:
        line = "".join("██" if cell else "  " for cell in row)
        lines.append(line)
    return "\n".join(lines)
