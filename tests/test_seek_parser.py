from src.chromecast_tui.app import _parse_seek_target


def test_parse_relative_positive():
    assert _parse_seek_target("+30", 50.0, 0.0) == 80.0


def test_parse_relative_negative_clamps_zero():
    assert _parse_seek_target("-30", 10.0, 0.0) == 0.0


def test_parse_mm_ss():
    assert _parse_seek_target("1:23", 0.0, 0.0) == 83.0


def test_parse_hh_mm_ss():
    assert _parse_seek_target("1:02:03", 0.0, 0.0) == 3723.0


def test_parse_absolute_seconds():
    assert _parse_seek_target("42", 0.0, 0.0) == 42.0


def test_parse_clamps_to_duration():
    assert _parse_seek_target("999", 0.0, 120.0) == 120.0


def test_parse_invalid_returns_none():
    assert _parse_seek_target("abc", 0.0, 0.0) is None
