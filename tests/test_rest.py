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
    b._rest_had_target = False
    b._last_progress = 0.0
    b.presses = []
    b._press = lambda label, key: b.presses.append(key)
    b._boss_near = lambda snap, now: boss
    b._rest_toggle = types.MethodType(Bot._rest_toggle, b)
    b.tick = types.MethodType(Bot._rest_tick, b)
    return b


def snap(state, auto_on, fill=None):
    return types.SimpleNamespace(state=state, auto_on=auto_on, frame=fill)


class RestTest(unittest.TestCase):
    def test_full_cycle(self):
        b = make_bot()
        b.hp = 0.48
        self.assertFalse(b.tick(snap("COMBAT", True), 0))       # 휴식 시작만 기록, 이번 틱은 평소대로 (50% 이하)
        self.assertEqual(b._rest, "finish")
        self.assertTrue(b.tick(snap("COMBAT", True), 5))        # 아직 싸우는 중 → 마무리 대기
        self.assertEqual(b.presses, [])
        self.assertTrue(b.tick(snap("IDLE", True), 10))         # 몹 처치 → T로 끄기
        self.assertEqual(b.presses, ["t"])
        self.assertTrue(b.tick(snap("IDLE", False), 11))        # 꺼짐 확인 → 회복 대기
        self.assertEqual(b._rest, "rest")
        b.hp = 0.8
        self.assertTrue(b.tick(snap("IDLE", False), 20))        # 80%는 아직 부족 → 계속 쉼
        self.assertEqual(b.presses, ["t"])
        b.hp = 0.99
        self.assertTrue(b.tick(snap("IDLE", False), 30))        # 98% 이상(=100%) → 다시 켜기
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


class TargetBarRestTest(unittest.TestCase):
    """센 지역: 몹이 계속 붙어 전투 상태가 안 끝나도, 타겟 체력바가 비면 자동 사냥을 끈다."""

    def setUp(self):
        import bot2.brain as brain
        self.brain = brain
        self.orig = brain.target_fill
        brain.target_fill = lambda frame: frame          # 테스트에서는 frame 자리에 채움 비율을 직접 넘김

    def tearDown(self):
        self.brain.target_fill = self.orig

    def test_waits_until_target_bar_empties_even_while_combat_continues(self):
        b = make_bot()
        b.hp = 0.4
        self.assertFalse(b.tick(snap("COMBAT", True, 0.8), 0))       # 타겟 체력바 80% → 휴식 시작, 잡던 몹 마무리
        self.assertEqual(b._rest, "finish")
        self.assertTrue(b._rest_had_target)
        self.assertTrue(b.tick(snap("COMBAT", True, 0.5), 3))        # 몹이 줄고 있음 → 계속 싸움
        self.assertTrue(b.tick(snap("COMBAT", True, 0.1), 6))
        self.assertEqual(b.presses, [])
        self.assertTrue(b.tick(snap("COMBAT", True, 0.0), 8))        # 바가 사라짐(몹 처치) → 전투가 계속돼도 T로 끈다
        self.assertEqual(b.presses, ["t"])

    def test_no_target_falls_back_to_combat_state(self):
        b = make_bot()
        b.hp = 0.4
        b.tick(snap("COMBAT", True, 0.0), 0)                         # 타겟 바가 처음부터 없음
        self.assertFalse(b._rest_had_target)
        self.assertTrue(b.tick(snap("COMBAT", True, 0.0), 3))        # 아직 전투 중 → 기다림
        self.assertTrue(b.tick(snap("IDLE", True, 0.0), 5))          # 전투 끝 → 끔
        self.assertEqual(b.presses, ["t"])


class FinishCapTest(unittest.TestCase):
    def setUp(self):
        import bot2.brain as brain
        self.brain = brain
        self.orig = brain.target_fill
        brain.target_fill = lambda frame: frame

    def tearDown(self):
        self.brain.target_fill = self.orig

    def test_low_hp_shortens_finish_wait(self):
        b = make_bot()
        b.hp = 0.45
        b.tick(snap("COMBAT", True, 0.9), 0)                          # 45%: 마무리 시작 (몹 체력 90%)
        self.assertEqual(b._rest, "finish")
        b.hp = 0.28                                                   # 30% 이하: 대기 10초로 단축
        self.assertTrue(b.tick(snap("COMBAT", True, 0.9), 5))         # 5초 → 아직 기다림
        self.assertTrue(b.tick(snap("COMBAT", True, 0.9), 11))        # 10초 지남 → 끄는 단계로 넘어감
        self.assertEqual(b._rest, "off")
        self.assertTrue(b.tick(snap("COMBAT", True, 0.9), 12))        # 다음 틱에 T로 끈다
        self.assertEqual(b.presses, ["t"])

    def test_very_low_hp_turns_off_immediately(self):
        b = make_bot()
        b.hp = 0.45
        b.tick(snap("COMBAT", True, 0.9), 0)
        b.hp = 0.15                                                   # 20% 이하: 바로 끈다
        self.assertTrue(b.tick(snap("COMBAT", True, 0.9), 1))         # 기다리지 않고 끄는 단계로
        self.assertEqual(b._rest, "off")
        self.assertTrue(b.tick(snap("COMBAT", True, 0.9), 5))         # (T 연타 방지 3초 간격이 지난 뒤 눌림)
        self.assertEqual(b.presses, ["t"])


if __name__ == "__main__":
    unittest.main()
