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
        cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
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


if __name__ == "__main__":
    unittest.main()
