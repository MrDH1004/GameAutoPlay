import unittest
from unittest.mock import patch

import numpy as np

from bot2.mission import MissionTask


class MissionPriorityTest(unittest.TestCase):
    def make_task(self, priority):
        task = MissionTask.__new__(MissionTask)
        task.priority = priority
        return task

    def test_configured_daily_priority(self):
        task = self.make_task(["일일", "본편", "서사", "주간"])
        self.assertEqual([name for name, _x in task.ordered_tabs()],
                         ["일일", "본편", "서사", "주간"])

    def test_only_checks_requested_priority(self):
        task = self.make_task(["일일", "본편", "서사", "주간"])
        self.assertEqual(task.ordered_tabs("일일", only=True), [("일일", 520)])

    def test_retry_preference_moves_tab_to_front(self):
        task = self.make_task(["일일", "본편", "서사", "주간"])
        self.assertEqual([name for name, _x in task.ordered_tabs("서사")],
                         ["서사", "일일", "본편", "주간"])

    @patch("tools.ocr.read_text")
    def test_daily_rows_skip_passive_meditation(self, read_text):
        read_text.return_value = [
            {"text": "[일일] 낙화의 전장 요귀 처치", "y": 50, "h": 20},
            {"text": "[일일] 일일 명상 수련하기", "y": 130, "h": 20},
            {"text": "[일일] 비경 탐험 참여", "y": 210, "h": 20},
        ]
        task = MissionTask.__new__(MissionTask)
        rows = task._daily_rows(np.zeros((1080, 1920, 3), dtype=np.uint8))
        self.assertEqual([text for text, _y in rows], [
            "[일일] 낙화의 전장 요귀 처치",
            "[일일] 비경 탐험 참여",
        ])

    def test_daily_guide_uses_safest_matching_region(self):
        task = MissionTask.__new__(MissionTask)
        task.cfg = {"daily_guide_fallback": True}
        spot = task._daily_guide_spot("[일일] 안개 기슭 요귀 처치")
        self.assertEqual(spot["name"], "안개 기슭 1")
        self.assertEqual(spot["power"], 37000)

    def test_daily_guide_does_not_route_unknown_event(self):
        task = MissionTask.__new__(MissionTask)
        task.cfg = {"daily_guide_fallback": True}
        self.assertIsNone(task._daily_guide_spot("[일일] 낙화의 전장 요귀 처치"))

    def test_nakhwa_progress_ocr_parser(self):
        self.assertEqual(MissionTask._parse_nakhwa_progress("낙화 요귀 1 / 100"), 1)
        self.assertEqual(MissionTask._parse_nakhwa_progress("100/100"), 100)
        self.assertIsNone(MissionTask._parse_nakhwa_progress("읽기 실패"))

    def test_missing_nakhwa_row_means_completed(self):
        self.assertTrue(MissionTask._nakhwa_row_completed([
            ("[일일] 안개 기슭 요귀 처치", 330),
            ("[일일] 황무지 마을 요귀 처치", 420),
        ]))
        self.assertFalse(MissionTask._nakhwa_row_completed([
            ("[일일] 낙화의 전장 요귀 처치", 250),
        ]))
        self.assertFalse(MissionTask._nakhwa_row_completed([]))

    def test_daily_quota_parser(self):
        self.assertEqual(MissionTask._parse_daily_quota("임무 완료 가능 개수 : 5 / 5"), (5, 5))
        self.assertEqual(MissionTask._parse_daily_quota("4/5"), (4, 5))
        self.assertIsNone(MissionTask._parse_daily_quota("읽기 실패"))


if __name__ == "__main__":
    unittest.main()
