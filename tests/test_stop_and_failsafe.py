"""정지(StopRequested)는 에러가 아니고, FAILSAFE로 매크로가 죽지 않는다."""
import logging
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot import actions  # noqa: E402
from bot2.collect import Collector  # noqa: E402
from bot2.farm import StopRequested  # noqa: E402


class FailSafeTest(unittest.TestCase):
    def test_failsafe_is_retried_once_instead_of_crashing(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise actions.FailSafeException("corner")
            return "ok"

        with mock.patch.object(actions.pydirectinput, "moveTo"):          # 마우스를 안쪽으로 옮기는 동작은 모의
            self.assertEqual(actions._safe(flaky), "ok")
        self.assertEqual(len(calls), 2)                                  # 한 번 실패 → 한 번 재시도

    def test_failsafe_flag_restored(self):
        def boom():
            raise actions.FailSafeException("corner")
        with mock.patch.object(actions.pydirectinput, "moveTo"):
            with self.assertRaises(actions.FailSafeException):            # 두 번째도 실패하면 그대로 올림
                actions._safe(boom)
        self.assertTrue(actions.pydirectinput.FAILSAFE)                  # 안전장치는 다시 켜져 있어야 함


class StopTest(unittest.TestCase):
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
