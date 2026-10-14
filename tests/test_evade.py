"""자리 회피: 방향 계산 / 상태 전이 (게임 없이)."""
import os
import sys
import types
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot2 import evade  # noqa: E402
from bot2.brain import Bot  # noqa: E402


class DirectionTest(unittest.TestCase):
    def test_flees_away_from_mobs(self):
        f = np.zeros((1080, 1920, 3), np.uint8)
        f[400:410, 300:500] = (30, 30, 220)                 # 왼쪽 위에 빨간 체력바(BGR) → 오른쪽 아래로 도망
        dx, dy = evade.flee_directions(f, 380)[0]
        self.assertGreater(dx, 0)
        self.assertGreater(dy, 0)

    def test_no_mobs_still_has_candidates(self):
        f = np.zeros((1080, 1920, 3), np.uint8)
        c = evade.flee_directions(f, 380)
        self.assertEqual(len(c), 7)
        self.assertEqual(c[0][0], 380)


def make_bot(hp, auto_on=True):
    b = types.SimpleNamespace()
    b.hp_cfg = {"enabled": True, "rest_enabled": True}
    b.dry_run = False
    b.hp = hp
    b._rest = None
    b._rest_at = b._rest_press_at = 0.0
    b._rest_tries = 0
    b._rest_had_target = False
    b._rest_empty_since = None
    b._last_progress = 0.0
    b._evade = None
    b._evade_pending = False
    b._evade_request = False
    b._evade_last = -1e9
    b.presses, b.calls = [], []
    b._press = lambda label, key: b.presses.append(key)
    b._boss_near = lambda snap, now: False
    b.window = types.SimpleNamespace(grab=lambda: "frame")
    b.evader = types.SimpleNamespace(flee=lambda fr: b.calls.append("flee") or (380, 0),
                                     back=lambda off: b.calls.append(("back", off)))
    b._rest_toggle = types.MethodType(Bot._rest_toggle, b)
    b.tick = types.MethodType(Bot._rest_tick, b)
    return b


def snap(auto_on, state="COMBAT"):
    return types.SimpleNamespace(state=state, auto_on=auto_on, frame=None, walking=False)


class EvadeFlowTest(unittest.TestCase):
    def test_low_hp_flee_then_return_then_resume(self):
        b = make_bot(0.25)
        self.assertTrue(b.tick(snap(True), 0))                    # HP 25% → 회피 시작 (T 끄는 중)
        self.assertEqual(b._rest, "off")
        self.assertTrue(b._evade_pending)
        self.assertTrue(b.tick(snap(True), 4))                    # T 누름
        self.assertEqual(b.presses, ["t"])
        self.assertTrue(b.tick(snap(False), 9))                   # 꺼짐 확인 → 지도로 이동
        self.assertEqual(b.calls, ["flee"])
        self.assertEqual(b._evade, (380, 0))
        self.assertEqual(b._rest, "rest")
        b.hp = 0.6
        self.assertTrue(b.tick(snap(False, "IDLE"), 20))          # 회복 중
        self.assertEqual(b.calls, ["flee"])
        b.hp = 1.0
        self.assertTrue(b.tick(snap(False, "IDLE"), 40))          # 회복 → 돌아가고 자동 사냥 켜기
        self.assertEqual(b.calls, ["flee", ("back", (380, 0))])
        self.assertIsNone(b._evade)
        self.assertEqual(b._rest, "on")

    def test_hp_above_threshold_does_not_evade(self):
        b = make_bot(0.40)
        b.tick(snap(True), 0)
        self.assertNotEqual(b._rest, "off")                       # 40%는 일반 휴식(잡던 몹 마무리)
        self.assertFalse(b._evade_pending)

    def test_manual_request_evades_at_full_hp(self):
        b = make_bot(1.0)
        b._evade_request = True
        self.assertTrue(b.tick(snap(True), 0))
        self.assertTrue(b._evade_pending)
        self.assertTrue(b.tick(snap(False), 1))
        self.assertEqual(b.calls, ["flee"])

    def test_attacked_while_resting_does_not_walk_back(self):
        b = make_bot(0.2)
        b.tick(snap(True), 0)
        b.tick(snap(False), 5)
        b.hp = 0.1
        b.tick(snap(False, "COMBAT"), 10)                         # 쉬는 중 공격받아 HP 10%
        self.assertEqual(b.calls, ["flee"])                       # 돌아가지 않음
        self.assertEqual(b._rest, "on")

    def test_cooldown(self):
        b = make_bot(0.2)
        b._evade_last = 100.0
        b.tick(snap(True), 120)                                   # 방금(20초 전) 회피함 → 다시 회피하지 않음
        self.assertFalse(b._evade_pending)


class AreaPickTest(unittest.TestCase):
    def test_parse_dist(self):
        self.assertEqual(evade.parse_dist("326m"), 326)
        self.assertEqual(evade.parse_dist("1,250 m"), 1250)
        self.assertIsNone(evade.parse_dist("축지술"))

    def test_pick_nearest_other_area_and_remember_current(self):
        areas = [{"name": "우물동굴 공터", "cy": 710, "dist": 12}, {"name": "아래광장", "cy": 784, "dist": 326},
                 {"name": "위쪽광장", "cy": 858, "dist": 180}, {"name": "비명동굴 가는길", "cy": 932, "dist": 1500}]
        here, tgt = evade.pick_target(areas)
        self.assertEqual(here["name"], "우물동굴 공터")
        self.assertEqual(tgt["name"], "위쪽광장")                 # 현재 지역 제외, MAX_DIST 안에서 가장 가까운 곳

    def test_no_reachable_area(self):
        self.assertEqual(evade.pick_target([{"name": "a", "cy": 1, "dist": 20}])[1], None)
        self.assertEqual(evade.pick_target([{"name": "a", "cy": 1, "dist": 5000}])[1], None)
        self.assertEqual(evade.pick_target([]), (None, None))

    def test_back_dispatches_on_token_type(self):
        calls = []
        ev = evade.Evader.__new__(evade.Evader)
        ev.back_area = lambda tok: calls.append(("area", tok)) or True
        ev._go = lambda offs: calls.append(("pin", offs)) or (1, 1)
        ev.back({"area": "아래광장", "dist": 300})
        ev.back((380, 0))
        self.assertEqual(calls[0][0], "area")
        self.assertEqual(calls[1], ("pin", [(-380, 0)]))


class DeathClearsEvadeTest(unittest.TestCase):
    def test_death_during_evade_drops_the_return_token(self):
        b = types.SimpleNamespace(cfg={"death": {"retry": 3.0, "free_buttons": []}}, _last_death_click=-1e9,
                                  _evade={"area": "아래광장", "dist": 61}, _evade_pending=False)
        b.matcher = types.SimpleNamespace(find=lambda *a, **k: None)
        b._death_open = False
        try:
            Bot._on_dead(b, types.SimpleNamespace(frame=None), 100.0)
        except Exception:                                    # 이 시험은 앞부분(복귀 기록 삭제)만 본다
            pass
        self.assertIsNone(b._evade)


if __name__ == "__main__":
    unittest.main()
