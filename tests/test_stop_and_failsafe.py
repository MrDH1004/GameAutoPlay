"""정지(StopRequested)는 에러가 아니고, 입력은 Cua Driver만 사용한다."""
import logging
import os
import sys
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot import actions  # noqa: E402
from bot.cua_input import CuaInputError  # noqa: E402
from bot2.brain import Bot  # noqa: E402
from bot2.collect import Collector  # noqa: E402
from bot2.farm import StopRequested  # noqa: E402


class CuaOnlyTest(unittest.TestCase):
    def test_input_without_cua_never_falls_back(self):
        old = actions._input_backend
        actions._input_backend = None
        try:
            with self.assertRaisesRegex(RuntimeError, "물리 입력 없이"):
                actions.press("t")
        finally:
            actions._input_backend = old


class StopTest(unittest.TestCase):
    @patch("bot2.brain.time.sleep")
    @patch("bot2.brain.selfcheck.run_all", return_value=([], "ok"))
    def test_daily_picker_cua_failure_is_retried_without_stopping_bot(self, _selfcheck, _sleep):
        bot = object.__new__(Bot)
        bot.cfg = {"window_title": "game", "loop_interval": 0.5, "stable_loop_interval": 2.0,
                   "focus_on_start": False}
        bot.stop_key, bot.pause_key = "f12", "f11"
        bot.dry_run = bot.paused = False
        bot.stop_event = Mock()
        bot.stop_event.is_set.side_effect = [False, True]
        bot.notifier = Mock()
        bot.hotkeys = Mock()
        bot.hotkeys.pressed.return_value = False
        bot.activity = Mock()
        bot.risk_guard = Mock()
        bot.live = Mock()
        bot._startup_check = Mock()
        bot._daily_report = Mock()
        bot._focus_manual_command = Mock(return_value=True)
        bot._apply_daily_mode = Mock()
        bot._apply_story_mode = Mock()
        bot._apply_normal_mode = Mock()
        bot._apply_manual_farm = Mock()
        failure = CuaInputError("temporary")
        bot._process_daily_picker = Mock(side_effect=failure)
        bot._retry_cua_input = Mock()

        bot.run()

        bot._retry_cua_input.assert_called_once_with(failure)
        bot.notifier.stop.assert_called_once()

    def test_input_error_after_stop_request_is_normal_exit(self):
        from gui import _is_normal_stop
        event = types.SimpleNamespace(is_set=lambda: True)
        bot = types.SimpleNamespace(stop_event=event)
        self.assertTrue(_is_normal_stop(RuntimeError("Cua click cancelled"), False, bot))
        self.assertTrue(_is_normal_stop(RuntimeError("startup cancelled"), True, None))
        self.assertFalse(_is_normal_stop(RuntimeError("real failure"), False, None))

    def test_wrapped_stop_requested_is_normal_exit(self):
        from gui import _is_normal_stop
        try:
            try:
                raise StopRequested()
            except StopRequested as stopped:
                raise RuntimeError("wrapped") from stopped
        except RuntimeError as error:
            self.assertTrue(_is_normal_stop(error))

    def test_exit_screen_is_skipped_while_stopping(self):
        clicks = []
        drv = types.SimpleNamespace(bot=types.SimpleNamespace(stop_event=types.SimpleNamespace(is_set=lambda: True)),
                                    click=lambda *a: clicks.append(a), sleep=lambda s: clicks.append(("sleep", s)))
        Collector(drv).exit_screen()
        self.assertEqual(clicks, [])                                     # 정지 중에는 클릭도 대기도 안 함

    def test_exit_screen_runs_normally(self):
        clicks = []
        drv = types.SimpleNamespace(bot=types.SimpleNamespace(stop_event=types.SimpleNamespace(is_set=lambda: False)),
                                    click=lambda *a: clicks.append(a), sleep=lambda s: clicks.append(("sleep", s)))
        Collector(drv).exit_screen()
        self.assertEqual(len(clicks), 2)


if __name__ == "__main__":
    unittest.main()
