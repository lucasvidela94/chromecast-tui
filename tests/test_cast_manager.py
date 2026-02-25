"""
Tests for CastManager logic that doesn't require a real Chromecast:
- playback state mapping
- volume clamping
- supported extension set
"""

import pytest
from unittest.mock import MagicMock, patch

from src.chromecast_tui.cast_manager import (
    CastManager,
    PlaybackState,
    SUPPORTED_EXTENSIONS,
    _host_from_url,
    _parse_ssdp_headers,
)


# ──────────────────────────────────────────────────────────────────────────────
# Supported extensions
# ──────────────────────────────────────────────────────────────────────────────

def test_common_video_extensions_supported():
    for ext in (".mp4", ".webm", ".mkv", ".avi", ".mov"):
        assert ext in SUPPORTED_EXTENSIONS, f"{ext} should be supported"


def test_common_audio_extensions_supported():
    for ext in (".mp3", ".flac", ".wav", ".ogg", ".opus", ".aac"):
        assert ext in SUPPORTED_EXTENSIONS, f"{ext} should be supported"


def test_common_image_extensions_supported():
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        assert ext in SUPPORTED_EXTENSIONS, f"{ext} should be supported"


def test_unsupported_extensions_not_included():
    for ext in (".exe", ".zip", ".pdf", ".docx", ".py"):
        assert ext not in SUPPORTED_EXTENSIONS, f"{ext} should not be supported"


# ──────────────────────────────────────────────────────────────────────────────
# PlaybackState defaults
# ──────────────────────────────────────────────────────────────────────────────

def test_playback_state_defaults():
    state = PlaybackState()
    assert state.status == "idle"
    assert state.title == ""
    assert state.current_time == 0.0
    assert state.duration == 0.0
    assert state.volume == 1.0
    assert state.is_muted is False


# ──────────────────────────────────────────────────────────────────────────────
# State mapping from pychromecast MediaStatus
# ──────────────────────────────────────────────────────────────────────────────

def _make_media_status(player_state, title="", current_time=0.0, duration=0.0):
    status = MagicMock()
    status.player_state = player_state
    status.title = title
    status.content_id = "http://example.com/file.mp4"
    status.current_time = current_time
    status.duration = duration
    return status


def test_state_mapping_playing():
    manager = CastManager()
    manager._on_media_status(_make_media_status("PLAYING"))
    assert manager.state.status == "playing"


def test_state_mapping_paused():
    manager = CastManager()
    manager._on_media_status(_make_media_status("PAUSED"))
    assert manager.state.status == "paused"


def test_state_mapping_buffering():
    manager = CastManager()
    manager._on_media_status(_make_media_status("BUFFERING"))
    assert manager.state.status == "buffering"


def test_state_mapping_idle():
    manager = CastManager()
    manager._on_media_status(_make_media_status("IDLE"))
    assert manager.state.status == "idle"


def test_state_mapping_unknown_defaults_to_idle():
    manager = CastManager()
    manager._on_media_status(_make_media_status("SOMETHING_NEW"))
    assert manager.state.status == "idle"


def test_state_stores_title():
    manager = CastManager()
    manager._on_media_status(_make_media_status("PLAYING", title="My Movie"))
    assert manager.state.title == "My Movie"


def test_state_stores_timing():
    manager = CastManager()
    manager._on_media_status(_make_media_status("PLAYING", current_time=42.5, duration=3600.0))
    assert manager.state.current_time == 42.5
    assert manager.state.duration == 3600.0


def test_state_callback_is_called():
    called_with = []
    manager = CastManager(on_state_change=lambda s: called_with.append(s))
    manager._on_media_status(_make_media_status("PLAYING", title="Test"))
    assert len(called_with) == 1
    assert called_with[0].status == "playing"


# ──────────────────────────────────────────────────────────────────────────────
# Volume clamping (set_volume calls cast.set_volume with a clamped value)
# ──────────────────────────────────────────────────────────────────────────────

def _manager_with_mock_cast():
    manager = CastManager()
    mock_cast = MagicMock()
    mock_cast.status.volume_level = 0.5
    mock_cast.status.volume_muted = False
    manager._cast = mock_cast
    return manager, mock_cast


def test_set_volume_normal():
    manager, mock_cast = _manager_with_mock_cast()
    manager.set_volume(0.7)
    mock_cast.set_volume.assert_called_once_with(0.7)
    assert manager.state.volume == 0.7


def test_set_volume_clamps_above_1():
    manager, mock_cast = _manager_with_mock_cast()
    manager.set_volume(1.5)
    mock_cast.set_volume.assert_called_once_with(1.0)
    assert manager.state.volume == 1.0


def test_set_volume_clamps_below_0():
    manager, mock_cast = _manager_with_mock_cast()
    manager.set_volume(-0.3)
    mock_cast.set_volume.assert_called_once_with(0.0)
    assert manager.state.volume == 0.0


def test_set_volume_no_cast_does_not_raise():
    manager = CastManager()
    # _cast is None — should be a no-op, not an exception
    manager.set_volume(0.5)


# ──────────────────────────────────────────────────────────────────────────────
# connected property
# ──────────────────────────────────────────────────────────────────────────────

def test_not_connected_by_default():
    manager = CastManager()
    assert manager.connected is False


def test_connected_when_cast_set():
    manager = CastManager()
    manager._cast = MagicMock()
    assert manager.connected is True


def test_device_name_empty_when_disconnected():
    manager = CastManager()
    assert manager.device_name == ""


def test_device_name_returns_cast_name():
    manager = CastManager()
    mock_cast = MagicMock()
    mock_cast.name = "Living Room"
    manager._cast = mock_cast
    assert manager.device_name == "Living Room"


def test_discover_supports_cast_info_without_host_attribute():
    manager = CastManager()

    cast_info = MagicMock()
    cast_info.friendly_name = "Living Room TV"
    cast_info.host = "192.168.1.42"
    cast_info.port = 8009
    cast_info.model_name = "Chromecast Ultra"
    cast_info.cast_type = "cast"

    cc = MagicMock(spec=["cast_info"])
    cc.cast_info = cast_info

    with patch("src.chromecast_tui.cast_manager.pychromecast.get_chromecasts", return_value=([cc], object())):
        with patch("src.chromecast_tui.cast_manager.pychromecast.stop_discovery"):
            with patch.object(manager, "_discover_roku", return_value=[]):
                devices = manager.discover(timeout=1.0)

    assert len(devices) == 1
    assert devices[0].name == "Living Room TV"
    assert devices[0].host == "192.168.1.42"
    assert devices[0].port == 8009
    assert devices[0].model_name == "Chromecast Ultra"
    assert devices[0].cast_type == "cast"
    assert devices[0].backend == "chromecast"


def test_parse_ssdp_headers_lowercases_keys():
    payload = (
        "HTTP/1.1 200 OK\r\n"
        "LOCATION: http://192.168.1.77:8060/\r\n"
        "USN: roku:ecp:abcdef\r\n\r\n"
    )
    headers = _parse_ssdp_headers(payload)
    assert headers["location"] == "http://192.168.1.77:8060/"
    assert headers["usn"] == "roku:ecp:abcdef"


def test_host_from_url_extracts_host():
    assert _host_from_url("http://192.168.1.77:8060/desc.xml") == "192.168.1.77"
    assert _host_from_url("https://roku.local/path") == "roku.local"
    assert _host_from_url("not-a-url") == ""
