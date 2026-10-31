"""웨이포인트 이동 중(하단 '자동 이동 중 N m' 바)에는 자동 사냥(T)을 켜지 않고 영기 수련도 열지 않는다."""
import os
import sys
import types
import unittest

import cv2
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bot.vision import Matcher  # noqa: E402
from bot2.brain import Bot  # noqa: E402
from bot2.perception import Perceiver  # noqa: E402


def snap(**kw):
    base = dict(walking=False, auto_on=False, combat=False, state="IDLE", frame=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


class WalkSignalTest(unittest.TestCase):
    def test_perceiver_sees_the_walking_bar(self):
        with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as stream:
            cfg = yaml.safe_load(stream)
        p = Perceiver(Matcher(os.path.join(ROOT, "templates"), 0.85), cfg)
        self.assertFalse(p.look(cv2.imread(os.path.join(ROOT, "tests/fixtures/field_hp_95.png")), 0.0).walking)
        self.assertTrue(p.look(cv2.imread(os.path.join(ROOT, "tests/fixtures/walking_bar.png")), 1.0).walking)


class GuardTest(unittest.TestCase):
    def make(self):
        b = types.SimpleNamespace()
        pressed = []
        b.pressed = pressed
        b._press = lambda label, key: pressed.append(key)
        b._want_hunt = True
        b._hunt_tries = 0
        b._hunt_press_at = 0.0
        b.cfg = {}
        b._walk_started = 0.0                                  # 수집 이동 중이 아님 (바 신호만으로 판단)
        b._walk_guard = types.MethodType(Bot._walk_guard, b)
        b.ensure = types.MethodType(Bot._ensure_hunt, b)
        return b

    def test_no_T_while_walking(self):
        b = self.make()
        b.ensure(snap(walking=True), 100.0)
        self.assertEqual(b.pressed, [])                       # 이동 중에는 누르지 않음
        self.assertTrue(b._want_hunt)                         # 요청은 남아 있음 → 도착 후에 켠다
        b.ensure(snap(walking=False), 101.0)
        self.assertEqual(b.pressed, ["t"])                    # 이동이 끝나면 켠다

    def test_already_on_is_noop(self):
        b = self.make()
        b.ensure(snap(walking=True, auto_on=True), 100.0)
        self.assertFalse(b._want_hunt)


class YeonggiGuardTest(unittest.TestCase):
    def make(self):
        b = types.SimpleNamespace()
        ran = []
        b.yeonggi = types.SimpleNamespace(due=lambda now: True, run=lambda now: ran.append(1))
        b.ran = ran
        b.stats = {"yeonggi": 0}
        b._idle_since = b._verify = None
        b.run_if_due = types.MethodType(Bot._run_yeonggi_if_due, b)
        return b

    def test_not_opened_while_walking_or_fighting(self):
        b = self.make()
        self.assertFalse(b.run_if_due(snap(walking=True), 0.0))
        self.assertFalse(b.run_if_due(snap(combat=True), 0.0))
        self.assertEqual(b.ran, [])
        self.assertTrue(b.run_if_due(snap(), 0.0))            # 조용하면 연다
        self.assertEqual(b.ran, [1])


class DebounceTest(unittest.TestCase):
    """이동 바는 이동 중에도 1~2초씩 사라진다(실측: 72초 관찰 중 6번 이상) → 그 틈에 T가 눌리면 안 된다."""

    def test_walking_flag_survives_a_short_flicker(self):
        with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as stream:
            cfg = yaml.safe_load(stream)
        p = Perceiver(Matcher(os.path.join(ROOT, "templates"), 0.85), cfg)
        bar = cv2.imread(os.path.join(ROOT, "tests/fixtures/walking_bar.png"))
        none = cv2.imread(os.path.join(ROOT, "tests/fixtures/field_hp_95.png"))
        self.assertTrue(p.look(bar, 10.0).walking)                 # 바 보임
        self.assertTrue(p.look(none, 11.5).walking)                # 1.5초 안 보여도 이동 중으로 봄
        self.assertTrue(p.look(none, 13.5).walking)                # 3.5초까지는 유지
        self.assertFalse(p.look(none, 15.0).walking)               # 5초 넘게 안 보이면 이동 끝

    def test_guard_holds_until_arrival(self):
        b = types.SimpleNamespace(cfg={"walk_end": 6.0, "walk_max": 600})
        b._walk_started, b._walk_last = 100.0, 100.0
        b.guard = types.MethodType(Bot._walk_guard, b)
        self.assertTrue(b.guard(snap(walking=True), 105.0))        # 이동 중
        self.assertTrue(b.guard(snap(walking=False), 108.0))       # 바가 3초 안 보임 → 아직 이동 중으로 봄
        self.assertFalse(b.guard(snap(walking=False), 112.0))      # 6초 넘게 안 보임 → 도착
        self.assertEqual(b._walk_started, 0.0)
        self.assertFalse(b.guard(snap(walking=False), 113.0))      # 이후에는 막지 않음

    def test_guard_gives_up_after_max(self):
        b = types.SimpleNamespace(cfg={"walk_end": 6.0, "walk_max": 600})
        b._walk_started, b._walk_last = 0.001, 0.001
        b.guard = types.MethodType(Bot._walk_guard, b)
        self.assertFalse(b.guard(snap(walking=True), 700.0))       # 10분 넘게 '이동 중'이면 비정상 → 풀어 줌

    def test_ensure_hunt_blocked_during_flicker(self):
        b = types.SimpleNamespace(cfg={"walk_end": 6.0, "walk_max": 600})
        pressed = []
        b._press = lambda label, key: pressed.append(key)
        b._want_hunt, b._hunt_tries, b._hunt_press_at = True, 0, 0.0
        b._walk_started, b._walk_last = 100.0, 100.0
        b._walk_guard = types.MethodType(Bot._walk_guard, b)
        b.ensure = types.MethodType(Bot._ensure_hunt, b)
        b.ensure(snap(walking=False), 103.0)                       # 바가 깜빡여 안 보이는 순간
        self.assertEqual(pressed, [])                              # 그래도 T를 누르지 않음
        b.ensure(snap(walking=False), 120.0)                       # 도착 후
        self.assertEqual(pressed, ["t"])


if __name__ == "__main__":
    unittest.main()
