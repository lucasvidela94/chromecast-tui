from src.chromecast_tui.app import _ascii_qr


def test_ascii_qr_returns_multiline_block_string():
    out = _ascii_qr("http://192.168.1.10:8765/remote")
    assert "\n" in out
    assert "██" in out
    lines = out.splitlines()
    assert len(lines) > 10
