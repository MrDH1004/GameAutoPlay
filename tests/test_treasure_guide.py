"""보물찾기 데이터와 수동 진행률 계산 검증."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import treasure_guide as guide  # noqa: E402


class TreasureGuideTest(unittest.TestCase):
    def test_catalog_totals(self):
        self.assertEqual(len(guide.REGIONS), 19)
        self.assertEqual(guide.TOTAL_CHESTS, 91)
        self.assertEqual(sum(r.normal for r in guide.REGIONS), 26)
        self.assertEqual(sum(r.rare for r in guide.REGIONS), 54)
        self.assertEqual(sum(r.legendary for r in guide.REGIONS), 11)

    def test_progress_is_sanitized_and_counted(self):
        raw = {"tabaro_castle": [1, 1, 12, 13, True], "unknown": [1]}
        self.assertEqual(guide.normalize_progress(raw), {"tabaro_castle": [1, 12]})
        self.assertEqual(guide.progress_summary(raw)[:2], (2, 91))

    def test_recommendation_respects_power_and_completion(self):
        region = guide.recommended_region(20_000, {"tabaro_castle": list(range(1, 13))})
        self.assertEqual(region.name, "신령의 연못")
        progress = {r.key: list(range(1, r.count + 1)) for r in guide.REGIONS[:8]}
        self.assertIsNone(guide.recommended_region(100_000, progress))
        self.assertEqual(guide.recommended_region(300_000, progress).name, "음산한 폐광")


if __name__ == "__main__":
    unittest.main()
