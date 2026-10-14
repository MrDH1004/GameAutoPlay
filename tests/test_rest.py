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
    b._rest_empty_since = None
    b._evade, b._evade_pending, b._evade_request, b._evade_last = None, False, False, -1e9
    b.hp_cfg["evade_enabled"] = False        # 이 파일은 일반 휴식만 시험한다 (자리 회피는 test_evade.py)
    b._last_progress = 0.0
    b.presses = []
    b._press = lambda label, key: b.presses.append(key)
    b._boss_near = lambda snap, now: boss
    b._rest_toggle = types.MethodType(Bot._rest_toggle, b)
    b.tick = types.MethodType(Bot._rest_tick, b)
    return b


def snap(state, auto_on, fill=None):
    return types.SimpleNamespace(state=state, auto_on=auto_on, frame=fill, walking=False)


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
        # 사용자 지시: 잡던 몹은 죽을 때까지 기다린다. 30초로는 끄지 않고, 비정상적으로 긴 5분만 포기 기준
        b = make_bot()
        b.hp = 0.3
        b.tick(snap("COMBAT", True), 0)
        self.assertTrue(b.tick(snap("COMBAT", True), 31))       # 30초가 지나도 전투 중이면 계속 기다림
        self.assertEqual(b.presses, [])
        self.assertTrue(b.tick(snap("COMBAT", True), 301))      # 5분 넘으면 끄는 단계로
        self.assertEqual(b._rest, "off")

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
        self.assertTrue(b.tick(snap("COMBAT", True, 0.0), 8))        # 바가 막 사라짐 → 0.8초 지켜봄
        self.assertEqual(b.presses, [])
        self.assertTrue(b.tick(snap("COMBAT", True, 0.0), 9))        # 계속 비어 있음(몹 처치) → 전투가 계속돼도 T로 끈다
        self.assertEqual(b.presses, ["t"])

    def test_nearly_dead_target_is_not_finished(self):
        """체력이 조금(2%) 남은 몹을 두고 끄면 안 되고, 바가 잠깐 깜빡여도 끄면 안 된다."""
        b = make_bot()
        b.hp = 0.4
        b.tick(snap("COMBAT", True, 0.8), 0)
        self.assertTrue(b.tick(snap("COMBAT", True, 0.015), 3))      # 바가 거의 비었지만 아직 살아 있음
        self.assertTrue(b.tick(snap("COMBAT", True, 0.0), 4))        # 1틱 깜빡임
        self.assertTrue(b.tick(snap("COMBAT", True, 0.3), 4.3))      # 다른 몹으로 타겟 전환
        self.assertEqual(b.presses, [])

    def test_no_target_falls_back_to_combat_state(self):
        b = make_bot()
        b.hp = 0.4
        b.tick(snap("COMBAT", True, 0.0), 0)                         # 타겟 바가 처음부터 없음
        self.assertFalse(b._rest_had_target)
        self.assertTrue(b.tick(snap("COMBAT", True, 0.0), 3))        # 아직 전투 중 → 기다림
        self.assertTrue(b.tick(snap("IDLE", True, 0.0), 5))          # 전투 끝 → 끔
        self.assertEqual(b.presses, ["t"])


class FinishUntilKillTest(unittest.TestCase):
    """사용자 지시: 잡던 몹은 HP·시간과 상관없이 죽을 때까지(타겟 체력바가 빌 때까지) 끄지 않는다."""

    def setUp(self):
        import bot2.brain as brain
        self.brain = brain
        self.orig = brain.target_fill
        brain.target_fill = lambda frame: frame

    def tearDown(self):
        self.brain.target_fill = self.orig

    def test_does_not_turn_off_while_target_alive_even_at_low_hp_or_long_time(self):
        b = make_bot()
        b.hp = 0.45
        b.tick(snap("COMBAT", True, 0.9), 0)
        b.hp = 0.12                                                   # HP가 아주 낮아도
        self.assertTrue(b.tick(snap("COMBAT", True, 0.9), 40))        # 40초가 지나도 몹이 살아 있으면 계속 싸움
        self.assertEqual(b._rest, "finish")
        self.assertEqual(b.presses, [])
        self.assertTrue(b.tick(snap("COMBAT", True, 0.0), 41))        # 몹이 죽으면(바가 빔) 그때 끈다
        self.assertTrue(b.tick(snap("COMBAT", True, 0.0), 42))        # 0.8초 넘게 계속 비어 있어야 확정
        self.assertEqual(b._rest, "off")

    def test_only_a_very_long_wait_gives_up(self):
        b = make_bot()
        b.hp = 0.45
        b.tick(snap("COMBAT", True, 0.9), 0)
        self.assertTrue(b.tick(snap("COMBAT", True, 0.9), 299))
        self.assertEqual(b._rest, "finish")
        self.assertTrue(b.tick(snap("COMBAT", True, 0.9), 301))       # 5분이 넘으면 비정상으로 보고 끔
        self.assertEqual(b._rest, "off")


if __name__ == "__main__":
    unittest.main()
