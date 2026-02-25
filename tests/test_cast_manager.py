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
