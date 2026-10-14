"""사냥터 선택: 길라잡이 순간이동은 권장 전투력을 달성해야 눌린다 → 허용 비율이 1을 넘어도 이동 대상은 내 전투력 이하."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot2.farm import FarmTask, guide_spots  # noqa: E402


def make(use_guide, margin):
    f = types.SimpleNamespace(db={"regions": {}, "spots": guide_spots()}, use_guide=use_guide, margin=margin, banned={})
    f._active_bans = lambda now: set()
    f.pick = types.MethodType(FarmTask.pick, f)
    return f


class PickTest(unittest.TestCase):
    def test_guide_travel_never_targets_above_own_power(self):
        f = make(True, 1.5)
        s = f.pick(100930, 0)
        self.assertLessEqual(s["power"], 100930)                 # 150K(1.5배)가 아니라 내 전투력 이하 중 가장 센 곳
        self.assertEqual(s["power"], 94500)

    def test_map_travel_can_use_the_full_margin(self):
        f = make(False, 1.5)
        f.db = {"regions": {}, "spots": guide_spots()}
        self.assertEqual(f.pick(100930, 0)["power"], 150000)

    def test_low_margin_is_respected_for_guide(self):
        f = make(True, 0.9)
        self.assertEqual(f.pick(100930, 0)["power"], 84500)      # 90.8K 이하 중 가장 센 곳


class ReadNumberTest(unittest.TestCase):
    def test_period_thousands_separator(self):
        import bot2.farm as farm
        orig = farm._ocr
        try:
            for txt, want in (("94.500", 94500), ("94,500", 94500), ("⚔ 84.500", 84500), ("150.000", 150000), ("1,150", 1150)):
                farm._ocr = lambda crop, scale=3, t=txt: t
                import numpy as np
                self.assertEqual(farm.read_number(np.zeros((1080, 1920, 3), np.uint8), (0, 0, 50, 20)), want, txt)
        finally:
            farm._ocr = orig


if __name__ == "__main__":
    unittest.main()
