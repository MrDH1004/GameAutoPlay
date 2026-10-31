"""사용자 조작 감지 토글: 끄면 _check_user가 통과하고, 켜면 다시 양보한다."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot.actions import UserInterrupt  # noqa: E402
from bot2.brain import Bot  # noqa: E402


def make(detect=True, idle=0.5):
    b = types.SimpleNamespace()
    b.user_detect = detect
    b.user_resume = 120
    b.waiting_user = True
    b._user_wait_until = 0.0
    b.dry_run = False
    b._idle_since = 1.0
    b._verify = {"ok": False}
    calls = []
    b.activity = types.SimpleNamespace(idle_seconds=lambda: idle, ignore_until_now=lambda s=1.5: calls.append(s))
    b.calls = calls
    b.check = types.MethodType(Bot._check_user, b)
    b.set = types.MethodType(Bot.set_user_detect, b)
    b.wait = types.MethodType(Bot._wait_for_user_control, b)
    return b


class UserDetectTest(unittest.TestCase):
    def test_check_follows_toggle(self):
        with self.assertRaises(UserInterrupt):
            make(detect=True).check()                      # 감지 켜짐: 조작 직후면 양보
        make(detect=False).check()                         # 감지 꺼짐: 통과

    def test_toggle_off_clears_waiting_and_on_ignores_click(self):
        b = make(detect=True)
        b.set(False)
        self.assertFalse(b.user_detect)
        self.assertFalse(b.waiting_user)                   # 조작 대기 중이었어도 바로 풀림
        b.set(True)
        self.assertTrue(b.user_detect)
        self.assertEqual(b.calls, [1.5])                   # 켜는 순간의 버튼 클릭은 조작으로 안 침
        b.set(True)
        self.assertEqual(b.calls, [1.5])                   # 같은 값 반복은 아무 일 없음

    def test_wait_survives_temporary_unknown_idle(self):
        values = iter((0.0, None, 121.0))
        b = make(detect=True)
        b.waiting_user = False
        b.activity.idle_seconds = lambda: next(values)
        self.assertTrue(b.wait(100.0))
        self.assertEqual(b._user_wait_until, 220.0)
        self.assertTrue(b.wait(110.0))                     # 포커스 변화로 None이어도 유지
        self.assertFalse(b.wait(221.0))
        self.assertFalse(b.waiting_user)

    def test_new_input_extends_two_minute_wait(self):
        values = iter((0.0, 0.0))
        b = make(detect=True)
        b.waiting_user = False
        b.activity.idle_seconds = lambda: next(values)
        self.assertTrue(b.wait(100.0))
        self.assertTrue(b.wait(150.0))
        self.assertEqual(b._user_wait_until, 270.0)


if __name__ == "__main__":
    unittest.main()
