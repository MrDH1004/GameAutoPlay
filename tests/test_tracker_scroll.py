import types
import unittest
from unittest.mock import patch

import numpy as np

from bot2.tracker import TrackerScanner


class TrackerScrollTest(unittest.TestCase):
    @patch("bot2.tracker.time.sleep")
    def test_main_quest_tab_uses_first_bottom_icon(self, _sleep):
        clicked = []
        scanner = TrackerScanner.__new__(TrackerScanner)
        scanner.bot = types.SimpleNamespace(
            window=types.SimpleNamespace(frame_w=1920, frame_h=1080),
            _click_frame=lambda label, x, y: clicked.append((label, x, y)))
        scanner.main_tab = [0.975, 0.113]
        scanner.main_tab_wait = 0.6
        scanner._main_tab_selected = False

        self.assertTrue(scanner.ensure_main_tab())
        self.assertEqual(clicked, [("트래커 메인 퀘스트 탭", 1872, 122)])
        self.assertFalse(scanner.ensure_main_tab())

    def test_main_tab_is_not_clicked_when_gold_selected_state_is_visible(self):
        clicked = []
        scanner = TrackerScanner.__new__(TrackerScanner)
        scanner.bot = types.SimpleNamespace(
            window=types.SimpleNamespace(frame_w=1920, frame_h=1080),
            _click_frame=lambda *args: clicked.append(args), live=None)
        scanner.main_tab = [0.975, 0.113]
        scanner.main_tab_wait = 0
        scanner._main_tab_selected = False
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[95:150, 1845:1905] = (20, 170, 230)

        self.assertFalse(scanner.ensure_main_tab(frame, force=True))
        self.assertEqual(clicked, [])
        self.assertTrue(scanner._main_tab_selected)

    def _scanner(self, before, after):
        scanner = TrackerScanner.__new__(TrackerScanner)
        scanner._crop = lambda: (before, 0, 0) if not hasattr(scanner, "_seen") else (after, 0, 0)
        original = scanner._crop

        def crop_once():
            result = original()
            scanner._seen = True
            return result

        scanner._crop = crop_once
        scanner._wheel = lambda _amount: None
        return scanner

    @patch("bot2.tracker.time.sleep")
    @patch.object(TrackerScanner, "_measure_shift", return_value=(0, 0.2))
    def test_failed_measurement_continues_when_screen_changed(self, _measure, _sleep):
        before = np.zeros((400, 300, 3), dtype=np.uint8)
        after = before.copy()
        after[20:80, 20:80] = 255
        scanner = self._scanner(before, after)
        self.assertGreaterEqual(scanner._scroll_measured(-36), 3)

    @patch("bot2.tracker.time.sleep")
    @patch.object(TrackerScanner, "_measure_shift", return_value=(0, 0.2))
    def test_failed_measurement_stops_only_on_same_screen(self, _measure, _sleep):
        frame = np.zeros((400, 300, 3), dtype=np.uint8)
        scanner = self._scanner(frame, frame.copy())
        self.assertEqual(scanner._scroll_measured(-36), 0)

    @patch("bot2.tracker.read_text")
    def test_click_text_clicks_verified_daily_row_not_external_row(self, read_text):
        read_text.return_value = [
            {"text": "[외전] 다른 임무", "x": 20, "y": 40, "w": 120, "h": 20},
            {"text": "[일일] 낙화의 전장 요귀 처치", "x": 30, "y": 140, "w": 200, "h": 20},
        ]
        clicked = []
        scanner = TrackerScanner.__new__(TrackerScanner)
        scanner.cfg = {"page_notches": 36}
        scanner.max_pages = 8
        scanner.bot = types.SimpleNamespace(
            _click_frame=lambda label, x, y: clicked.append((label, x, y)))
        scanner._to_top = lambda: None
        scanner._crop = lambda: (np.zeros((400, 300, 3), dtype=np.uint8), 1500, 60)

        self.assertTrue(scanner.click_text("[일일]"))
        self.assertEqual(clicked, [("트래커 [일일]", 1580, 210)])

    @patch("bot2.tracker.read_text")
    def test_click_daily_survives_broken_prefix_and_scrolls_main_list(self, read_text):
        read_text.side_effect = [
            [{"text": "[6장] 폐광의 요수", "x": 20, "y": 40, "w": 150, "h": 20}],
            [{"text": "하퉆주자日 영역 요귀 처치", "x": 25, "y": 150, "w": 220, "h": 20}],
        ]
        clicked, scrolls = [], []
        scanner = TrackerScanner.__new__(TrackerScanner)
        scanner.cfg = {"page_notches": 36}
        scanner.max_pages = 8
        scanner.bot = types.SimpleNamespace(
            _click_frame=lambda label, x, y: clicked.append((label, x, y)))
        scanner._to_top = lambda: None
        scanner._crop = lambda: (np.zeros((400, 300, 3), dtype=np.uint8), 1060, 46)
        scanner._scroll_measured = lambda amount: scrolls.append(amount) or 180

        title = scanner.click_daily("[일일] 산신의 영역 요귀 처치")

        self.assertEqual(title, "[일일] 산신의 영역 요귀 처치")
        self.assertEqual(scrolls, [-36])
        self.assertEqual(clicked, [("트래커 [일일]", 1140, 206)])

    @patch("bot2.tracker.read_text")
    def test_click_daily_skips_group_header_and_remaining_count(self, read_text):
        read_text.return_value = [
            {"text": "0일일 임무", "x": 20, "y": 40, "w": 140, "h": 20},
            {"text": "황무지마을 남은 일일 임무 5개", "x": 20, "y": 80, "w": 240, "h": 20},
            {"text": "[6장] 폐광의 요수 2", "x": 20, "y": 140, "w": 180, "h": 20},
            {"text": "[일일] 낙화의 전장 요귀 처치", "x": 30, "y": 300, "w": 240, "h": 20},
        ]
        clicked = []
        scanner = TrackerScanner.__new__(TrackerScanner)
        scanner.cfg = {"page_notches": 36}
        scanner.max_pages = 8
        scanner.bot = types.SimpleNamespace(
            _click_frame=lambda label, x, y: clicked.append((label, x, y)))
        scanner._to_top = lambda: None
        scanner._crop = lambda: (np.zeros((430, 380, 3), dtype=np.uint8), 1500, 60)

        title = scanner.click_daily()

        self.assertIn("낙화의 전장", title)
        self.assertEqual(clicked, [("트래커 [일일]", 1580, 370)])

    @patch("bot2.tracker.read_text")
    def test_click_daily_skips_secret_realm_time_notice(self, read_text):
        read_text.return_value = [
            {"text": "비경탐험 시간이 남아 있어요.", "x": 20, "y": 80, "w": 240, "h": 20},
            {"text": "[일일] 낙화의 전장 요귀 처치", "x": 30, "y": 300, "w": 240, "h": 20},
        ]
        clicked = []
        scanner = TrackerScanner.__new__(TrackerScanner)
        scanner.cfg = {"page_notches": 36}
        scanner.max_pages = 8
        scanner.bot = types.SimpleNamespace(
            _click_frame=lambda label, x, y: clicked.append((label, x, y)))
        scanner._to_top = lambda: None
        scanner._crop = lambda: (np.zeros((430, 380, 3), dtype=np.uint8), 1500, 60)

        title = scanner.click_daily()

        self.assertIn("낙화의 전장", title)
        self.assertEqual(clicked, [("트래커 [일일]", 1580, 370)])

    @patch("bot2.tracker.read_text")
    def test_click_daily_only_uses_checked_title_when_allowed(self, read_text):
        read_text.return_value = [
            {"text": "[일일] 황무지 마을 요귀 처치", "x": 20, "y": 100, "w": 240, "h": 20},
            {"text": "[일일] 비경 탐험 참여", "x": 20, "y": 220, "w": 220, "h": 20},
        ]
        clicked = []
        scanner = TrackerScanner.__new__(TrackerScanner)
        scanner.cfg = {"page_notches": 36}
        scanner.max_pages = 2
        scanner.bot = types.SimpleNamespace(
            _click_frame=lambda label, x, y: clicked.append((label, x, y)))
        scanner._to_top = lambda: None
        scanner._crop = lambda: (np.zeros((430, 380, 3), dtype=np.uint8), 1500, 60)

        title = scanner.click_daily("", allowed_titles=["[일일] 비경 탐험 참여"])

        self.assertEqual(title, "[일일] 비경 탐험 참여")
        self.assertEqual(clicked, [("트래커 [일일]", 1580, 290)])

    @patch("bot2.tracker.time.sleep")
    @patch("bot2.tracker.read_text")
    def test_click_daily_expands_collapsed_daily_group_once(self, read_text, _sleep):
        read_text.side_effect = [
            [{"text": "오늘 남은 일일 임무 5개", "x": 20, "y": 80, "w": 220, "h": 20}],
            [{"text": "[일일] 우물 동굴 요귀 처치", "x": 30, "y": 180, "w": 220, "h": 20}],
        ]
        clicked = []
        scanner = TrackerScanner.__new__(TrackerScanner)
        scanner.cfg = {"page_notches": 36}
        scanner.max_pages = 2
        scanner.bot = types.SimpleNamespace(
            _click_frame=lambda label, x, y: clicked.append((label, x, y)))
        scanner._to_top = lambda: None
        scanner._crop = lambda: (np.zeros((430, 380, 3), dtype=np.uint8), 1500, 60)
        scanner._scroll_measured = lambda _amount: 0

        title = scanner.click_daily("[일일] 우물 동굴 요귀 처치", strict=True)

        self.assertEqual(title, "[일일] 우물 동굴 요귀 처치")
        self.assertEqual([label for label, _x, _y in clicked],
                         ["트래커 일일 그룹 펼치기", "트래커 [일일]"])


if __name__ == "__main__":
    unittest.main()
