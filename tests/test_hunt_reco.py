"""사냥터 추천: 내 전투력에 맞는 곳 / 다음 목표 계산."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gear_guide as g  # noqa: E402


class HuntRecoTest(unittest.TestCase):
    def test_macro_uses_margin_and_web_uses_full_power(self):
        r = g.recommend_hunt(75000, 0.9)                 # 75,000 x 0.9 = 67,500 → 산신의 영역 1(68,000)은 아직 못 감
        self.assertEqual(r["macro"][0], "안개 기슭 지하 동굴")
        self.assertEqual(r["web"][0], "산신의 영역 1")      # 웹 원칙은 권장 전투력만 넘으면 됨
        self.assertEqual(r["next"][0], "산신의 영역 2")

    def test_too_weak_has_no_pick(self):
        r = g.recommend_hunt(30000)
        self.assertIsNone(r["macro"])
        self.assertIsNone(r["web"])
        self.assertEqual(r["next"][0], "안개 기슭 1")

    def test_late_zones_are_not_movable_by_macro(self):
        r = g.recommend_hunt(1_000_000, 0.9)
        self.assertEqual(r["web"][0], "고갯마루")
        self.assertFalse(r["web"][2])
        self.assertEqual(r["macro"][0], "독기 지대 2")       # 도깨비헬퍼가 이동할 수 있는 가장 높은 곳
        self.assertIsNone(r["next"])

    def test_ladder_sorted(self):
        p = [x[1] for x in g.hunt_ladder()]
        self.assertEqual(p, sorted(p))


if __name__ == "__main__":
    unittest.main()
