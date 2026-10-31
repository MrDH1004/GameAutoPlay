"""실시간 상황판: 잡는 몹/퀘스트 읽기와 동기화 요약."""
import os
import sys
import time
import types
import unittest
from collections import deque
import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from bot2 import livestatus as ls  # noqa: E402


def load(name):
    return cv2.imread(os.path.join(ROOT, "tests", "fixtures", name))


class LiveStatusTest(unittest.TestCase):
    def test_sync_rate_and_reason(self):
        bot = types.SimpleNamespace(paused=False, waiting_user=False,
                                    window=types.SimpleNamespace(locate=lambda: True, is_minimized=lambda: False,
                                                                 is_foreground=lambda: True))
        live = ls.LiveStatus(bot)
        for known in (True, True, True, False):
            live.note_frame(known)
        sy = live.sync()
        self.assertAlmostEqual(sy["rate"], 0.75)
        self.assertEqual(sy["why"], "")
        bot.window.is_foreground = lambda: False
        self.assertEqual(live.sync()["why"], "게임 창이 앞이 아님")
        bot.waiting_user = True
        self.assertEqual(live.sync()["why"], "사용자 조작 대기")
        bot.paused = True
        self.assertEqual(live.sync()["why"], "일시정지")

    def test_no_data_before_first_frame(self):
        bot = types.SimpleNamespace(paused=False, waiting_user=False,
                                    window=types.SimpleNamespace(locate=lambda: True, is_minimized=lambda: False,
                                                                 is_foreground=lambda: True))
        sy = ls.LiveStatus(bot).sync()
        self.assertIsNone(sy["rate"])                        # 아직 화면을 한 번도 못 본 상태: 비율을 만들어 내지 않음
        self.assertIsNone(sy["age"])

    def test_read_quest_top(self):
        q = ls.read_quest(load("field_hp_95.png"))
        self.assertTrue(q)                                   # 트래커 맨 위에 퀘스트 제목이 있음

    def test_refresh_reuses_recent_bot_frame(self):
        frame = np.zeros((1080, 1920, 3), np.uint8)
        window = types.SimpleNamespace(locate=lambda: True, is_minimized=lambda: False,
                                       grab=lambda: self.fail("최근 프레임이 있는데 다시 캡처함"))
        bot = types.SimpleNamespace(paused=False, waiting_user=False, window=window, cfg={},
                                    stop_event=types.SimpleNamespace(is_set=lambda: False),
                                    _in_field=lambda _frame: False)
        live = ls.LiveStatus(bot, interval=3.0)
        live.note_frame(False, frame)
        live.refresh()
        self.assertIs(live._latest_frame, frame)
        self.assertFalse(live.data["field"])


if __name__ == "__main__":
    unittest.main()
