from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot import actions
from bot.cua_input import CuaInput, CuaInputError, find_executable


class Window:
    hwnd = 77
    left = 31
    top = 45

    def locate(self):
        return True

    def is_minimized(self):
        return False


def _backend(tmp_path):
    with patch("bot.cua_input.find_executable", return_value="cua-driver.exe"):
        backend = CuaInput(Window())
    backend._capture_dir = tmp_path
    backend._capture_file = tmp_path / "latest.png"
    return backend


def test_find_executable_prefers_bundled_driver(tmp_path):
    bundled = tmp_path / "cua-driver" / "cua-driver.exe"
    bundled.parent.mkdir()
    bundled.write_bytes(b"driver")
    with patch("bot.cua_input.RESOURCE_ROOT", tmp_path), \
            patch("bot.cua_input.shutil.which", return_value=r"C:\installed\cua-driver.exe"):
        assert find_executable() == str(bundled)


def test_click_uses_foreground_cua_and_restores_cursor(tmp_path):
    backend = _backend(tmp_path)
    backend._target = lambda: (123, 77)
    backend._observe = lambda *_: {
        "capture_id": "capture-1", "window_bounds": {"x": 30, "y": 14}
    }
    prepared = []
    restored = []
    backend._prepare_foreground = lambda hwnd: prepared.append(hwnd) or True
    backend._cursor_position = lambda: (700, 500)
    backend._restore_cursor = lambda point: restored.append(point)
    calls = []
    backend._call = lambda tool, payload: calls.append((tool, payload)) or {"steps": [{"ok": True}]}

    backend.click(130, 214)

    payload = calls[0][1]
    assert payload["pid"] == 123 and payload["window_id"] == 77
    assert payload["foreground_fallback"] is False
    assert payload["delivery_mode"] == "foreground"
    assert payload["steps"][0]["args"] == {
        "x": 99, "y": 169, "delivery_mode": "foreground"
    }
    assert prepared == [77]
    assert restored == [(700, 500)]


def test_click_restores_cursor_even_when_cua_fails(tmp_path):
    backend = _backend(tmp_path)
    backend._target = lambda: (123, 77)
    restored = []
    backend._prepare_foreground = lambda _hwnd: True
    backend._cursor_position = lambda: (321, 654)
    backend._restore_cursor = lambda point: restored.append(point)
    backend._observe = lambda *_: {
        "capture_id": "fresh", "window_bounds": {"x": 30, "y": 14}
    }
    backend._call = lambda *_: (_ for _ in ()).throw(CuaInputError("failed"))

    with pytest.raises(CuaInputError):
        backend.click(130, 214)

    assert restored == [(321, 654)]


def test_hotkey_uses_posted_key_with_modifiers(tmp_path):
    backend = _backend(tmp_path)
    backend._target = lambda: (123, 77)
    backend._observe = lambda *_: {}
    calls = []
    backend._call = lambda tool, payload: calls.append((tool, payload)) or {"steps": [{"ok": True}]}

    backend.hotkey("ctrl+1")

    assert calls[0][1]["steps"][0] == {
        "tool": "press_key",
        "args": {"key": "1", "delivery_mode": "foreground", "modifiers": ["ctrl"]},
    }


def test_wheel_uses_background_drag_for_unity(tmp_path):
    backend = _backend(tmp_path)
    backend._target = lambda: (123, 77)
    backend._observe = lambda *_: {
        "window_bounds": {"x": 30, "y": 14}, "screenshot_height": 784
    }
    calls = []
    backend._call = lambda tool, payload: calls.append((tool, payload)) or {"steps": [{"ok": True}]}

    backend.wheel(1130, 314, -36)

    step = calls[0][1]["steps"][0]
    assert step["tool"] == "drag"
    assert step["args"]["delivery_mode"] == "background"
    assert step["args"]["to_y"] < step["args"]["from_y"]


def test_foreground_steps_prepare_exact_game_window(tmp_path):
    backend = _backend(tmp_path)
    prepared = []
    backend._prepare_foreground = lambda hwnd: prepared.append(hwnd) or True
    backend._call = lambda *_: {"steps": [{"ok": True}]}

    backend._steps(123, 77, [{"tool": "press_key", "args": {"key": "t"}}], "foreground")

    assert prepared == [77]


def test_actions_do_not_fallback_when_cua_fails():
    failing = SimpleNamespace(click=lambda *_, **__: (_ for _ in ()).throw(CuaInputError("failed")))
    old = actions._input_backend
    actions._input_backend = failing
    try:
        with pytest.raises(CuaInputError):
            actions.click(10, 20, jitter=0)
    finally:
        actions._input_backend = old


def test_actions_refuse_input_without_cua():
    old = actions._input_backend
    actions._input_backend = None
    try:
        with pytest.raises(CuaInputError, match="물리 입력 없이"):
            actions.click(10, 20)
    finally:
        actions._input_backend = old


def test_expired_session_is_revived_once(tmp_path):
    backend = _backend(tmp_path)
    expired = SimpleNamespace(returncode=1, stdout="", stderr="session has ended")
    success = SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")
    backend._start_session = lambda: None
    with patch("bot.cua_input.subprocess.run", side_effect=[expired, success]) as run:
        assert backend._call("get_window_state", {"pid": 1, "window_id": 2})["ok"] is True
    assert run.call_count == 2


def test_failed_step_log_keeps_reason_and_omits_large_observation(tmp_path):
    backend = _backend(tmp_path)
    failed = {
        "executed": 1,
        "failed_step": 0,
        "observation": {"state": {"capture_id": "very-large-capture"}},
        "steps": [{"ok": False, "message": "foreground target is not active"}],
    }
    response = SimpleNamespace(returncode=1, stdout=__import__("json").dumps(failed), stderr="")

    with patch("bot.cua_input.subprocess.run", return_value=response):
        with pytest.raises(CuaInputError) as exc:
            backend._call("run_steps", {"pid": 1, "window_id": 2})

    assert "foreground target is not active" in str(exc.value)
    assert "very-large-capture" not in str(exc.value)


def test_session_disables_cursor_motion_animation(tmp_path):
    backend = _backend(tmp_path)
    started = SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")
    configured = SimpleNamespace(returncode=0, stdout='{"ok": true}', stderr="")
    with patch("bot.cua_input.subprocess.run", side_effect=[started, configured]) as run:
        backend._start_session()

    payload = __import__("json").loads(run.call_args_list[1].kwargs["input"])
    assert payload["timing"] == "fixed"
    assert payload["glide_duration_ms"] == 1
    assert payload["dwell_after_click_ms"] == 0
    assert all(value == "off" for value in payload["effects"].values())


def test_check_uses_screen_capture_and_reserves_cua_for_input(tmp_path):
    backend = _backend(tmp_path)
    backend.window.capture_backend = backend.capture
    backend.window.background_capture = True
    running = SimpleNamespace(returncode=0, stdout="daemon is running", stderr="")
    backend._start_session = lambda: None
    with patch("bot.cua_input.subprocess.run", return_value=running):
        backend.check()
    assert backend.window.capture_backend is None
    assert backend.window.capture_fallback == backend.capture
    assert backend.window.background_capture is False
    assert backend.window.cua_input_enabled is True


def test_close_ends_only_helper_cua_session_and_clears_capture(tmp_path):
    backend = _backend(tmp_path)
    backend.window.capture_backend = backend.capture
    backend.window.background_capture = True
    backend.window.cua_input_enabled = True
    backend._capture_file.write_bytes(b"capture")
    ended = SimpleNamespace(returncode=0, stdout='{"active": false}', stderr="")

    with patch("bot.cua_input.subprocess.run", return_value=ended) as run:
        backend.close()
        backend.close()

    assert run.call_count == 1
    assert run.call_args.args[0][-1] == "end_session"
    assert __import__("json").loads(run.call_args.kwargs["input"])["session"] == "dokkaebi-helper"
    assert not backend._capture_file.exists()
    assert backend.window.capture_backend is None
    assert backend.window.capture_fallback is None
    assert backend.window.background_capture is False
    assert backend.window.cua_input_enabled is False


def test_clear_input_backend_closes_owned_backend():
    closed = []
    old = actions._input_backend
    actions._input_backend = SimpleNamespace(close=lambda: closed.append(True))
    try:
        actions.clear_input_backend()
        assert actions._input_backend is None
        assert closed == [True]
    finally:
        actions._input_backend = old
