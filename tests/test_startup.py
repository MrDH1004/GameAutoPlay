"""시작 점검: 저장된 실제 화면으로 지도 이름·퀘스트 줄 수를 읽고, 요약/경고 문구를 만든다."""
import os
import sys
import unittest

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bot2 import startup as su  # noqa: E402


def load(name):
    return cv2.imread(os.path.join(ROOT, "tests", "fixtures", name))


class StartupTest(unittest.TestCase):
    def test_map_name_without_icon_prefix(self):
        name = su.read_map_name(load("field_hp_95.png"))
        self.assertIn("지하", name)
        self.assertIn("동굴", name)
        self.assertFalse(name[:1].isdigit())                     # 방패 아이콘이 숫자로 붙지 않음

    def test_quest_lines(self):
        q = su.count_tracker_lines(load("field_hp_95.png"))
        self.assertGreaterEqual(q["total"], 1)

    def test_describe_and_warnings(self):
        st = {"screen": "필드", "map": "우물동굴", "hp": 0.4, "power": 66000, "potions": 7,
              "quests": {"main": 1, "other": 3, "total": 4}, "auto_on": True, "took": 1.2}
        text = su.describe(st)
        for piece in ("우물동굴", "40%", "66,000", "물약 7개", "퀘스트 4개", "자동 사냥 켜짐"):
            self.assertIn(piece, text)
        warns = " ".join(su.warnings(st, 50))
        self.assertIn("물약 7개", warns)
        self.assertIn("HP 40%", warns)
        self.assertEqual(su.warnings({"screen": "사망 화면"}, 50)[0][:6], "필드가 아님")

    def test_clean_map_name(self):
        self.assertEqual(su.clean_map_name("9 안개 기습 지하 동굴"), "안개 기습 지하 동굴")             # 앞의 방패 아이콘
        self.assertEqual(su.clean_map_name("비경] 영험한청산지하동굴 00:59:58"), "[비경] 영험한청산지하동굴")  # 던전 남은 시간 + 대괄호 복원
        self.assertEqual(su.clean_map_name("우물동굴"), "우물동굴")

    def test_quest_title_reads_main_quest_not_reward_widget(self):
        from bot2 import livestatus as ls
        self.assertEqual(ls.read_quest(load("field_hp_95.png")), "[5장] 길목을 막는 요귀")


class PotionCountVoteTest(unittest.TestCase):
    """물약 개수는 한 번만 읽으면 '204'를 '2'/'304'로 잘못 읽을 수 있다 → 여러 방식의 다수결."""

    def read(self, outputs):
        import numpy as np
        import bot2.shop as shop
        it = iter(outputs)
        orig = shop._ocr
        try:
            shop._ocr = lambda img, sc=3: next(it)
            return shop._read_count(np.zeros((24, 40, 3), np.uint8))
        finally:
            shop._ocr = orig

    def test_majority_wins_over_a_single_misread(self):
        self.assertEqual(self.read(["204", "304", "204", "204", "204"]), 204)
        self.assertEqual(self.read(["2", "204", "204", "", "204"]), 204)

    def test_none_when_nothing_readable(self):
        self.assertIsNone(self.read(["", "", "", "", ""]))


if __name__ == "__main__":
    unittest.main()
