"""HP 휴식 상태 머신: HP 1/3 이하 → 잡던 몹 마무리 → 자동 사냥 끔 → 80% 회복 → 다시 켬. 보스면 쉬지 않음."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot2.brain import Bot  # noqa: E402


def make_bot(boss=False):
    b = types.SimpleNamespace()
    b.hp_cfg = {"enabled": True, "rest_enabled": True}
    b.dry_run = False
    b.hp = 1.0
    b._rest = None
    b._rest_at = b._rest_press_at = 0.0
    b._rest_tries = 0
    b._last_progress = 0.0
    b.presses = []
    b._press = lambda label, key: b.presses.append(key)
    b._boss_near = lambda snap, now: boss
    b._rest_toggle = types.MethodType(Bot._rest_toggle, b)
    b.tick = types.MethodType(Bot._rest_tick, b)
    return b


def snap(state, auto_on):
    return types.SimpleNamespace(state=state, auto_on=auto_on)


class RestTest(unittest.TestCase):
    def test_full_cycle(self):
        b = make_bot()
        b.hp = 0.30
        self.assertFalse(b.tick(snap("COMBAT", True), 0))       # 휴식 시작만 기록, 이번 틱은 평소대로
        self.assertEqual(b._rest, "finish")
        self.assertTrue(b.tick(snap("COMBAT", True), 5))        # 아직 싸우는 중 → 마무리 대기
        self.assertEqual(b.presses, [])
        self.assertTrue(b.tick(snap("IDLE", True), 10))         # 몹 처치 → T로 끄기
        self.assertEqual(b.presses, ["t"])
        self.assertTrue(b.tick(snap("IDLE", False), 11))        # 꺼짐 확인 → 회복 대기
        self.assertEqual(b._rest, "rest")
        b.hp = 0.6
        self.assertTrue(b.tick(snap("IDLE", False), 20))
        self.assertEqual(b.presses, ["t"])
        b.hp = 0.85
        self.assertTrue(b.tick(snap("IDLE", False), 30))        # 80% 이상 → 다시 켜기
        self.assertEqual(b.presses, ["t", "t"])
        self.assertFalse(b.tick(snap("IDLE", True), 31))        # 켜짐 확인 → 끝
        self.assertIsNone(b._rest)

    def test_boss_no_rest(self):
        b = make_bot(boss=True)
        b.hp = 0.2
        self.assertFalse(b.tick(snap("COMBAT", True), 0))
        self.assertIsNone(b._rest)

    def test_finish_timeout(self):
        b = make_bot()
        b.hp = 0.3
        b.tick(snap("COMBAT", True), 0)
        self.assertTrue(b.tick(snap("COMBAT", True), 31))       # 30초 넘게 전투 → 그냥 끈다
        self.assertEqual(b.presses, ["t"])

    def test_rest_max_and_attacked(self):
        b = make_bot()
        b.hp = 0.3
        b.tick(snap("COMBAT", True), 0)
        b.tick(snap("IDLE", True), 1)
        b.tick(snap("IDLE", False), 2)
        self.assertEqual(b._rest, "rest")
        b.hp = 0.1
        self.assertTrue(b.tick(snap("COMBAT", False), 5))       # 쉬는 중 맞아서 15% 아래 → 다시 켬
        self.assertEqual(b._rest, "on")
        self.assertEqual(b.presses[-1], "t")


if __name__ == "__main__":
    unittest.main()
