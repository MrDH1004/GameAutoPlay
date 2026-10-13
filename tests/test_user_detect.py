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
    calls = []
    b.activity = types.SimpleNamespace(idle_seconds=lambda: idle, ignore_until_now=lambda s=1.5: calls.append(s))
    b.calls = calls
    b.check = types.MethodType(Bot._check_user, b)
    b.set = types.MethodType(Bot.set_user_detect, b)
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


if __name__ == "__main__":
    unittest.main()
