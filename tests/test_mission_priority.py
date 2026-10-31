import os
import tempfile
import types
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

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

    def test_normal_mode_request_blocks_mission_before_opening(self):
        task = MissionTask.__new__(MissionTask)
        task.bot = MagicMock(normal_mode=False, _normal_mode_request=True)
        task.bot._press = MagicMock()

        self.assertFalse(task.run(1.0))
        task.bot._press.assert_not_called()

    def test_only_checks_requested_priority(self):
        task = self.make_task(["일일", "본편", "서사", "주간"])
        self.assertEqual(task.ordered_tabs("일일", only=True), [("일일", 520)])

    def test_retry_preference_moves_tab_to_front(self):
        task = self.make_task(["일일", "본편", "서사", "주간"])
        self.assertEqual([name for name, _x in task.ordered_tabs("서사")],
                         ["서사", "일일", "본편", "주간"])

    def test_story_safe_mode_selects_suitable_row_before_auto_progress(self):
        task = MissionTask.__new__(MissionTask)
        task.cfg = {"badge_threshold": 0.85}
        task.t = {"ok_badge": "ok.png"}
        task.regions = {"list": None}
        selected = {"value": False}
        clicks = []
        task.bot = types.SimpleNamespace(
            window=types.SimpleNamespace(frame_w=1920, frame_h=1080),
            matcher=types.SimpleNamespace(find_all=lambda *_a, **_k: [types.SimpleNamespace(y=320)]))
        task._grab = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._click_xy = lambda label, *_a: (clicks.append(label), selected.__setitem__("value", True))
        task._find = lambda _frame, key, *_a, **_k: (
            types.SimpleNamespace(x=1700, y=980) if key == "auto" and selected["value"] else None)
        task._on_screen = lambda: False
        task._progressing = lambda: True

        self.assertTrue(task._try_tab("서사", 331, safe_only=True))
        self.assertEqual(clicks[1:], ["서사 적합 퀘스트 선택", "자동 진행"])

    def test_story_safe_mode_never_uses_selected_unsafe_auto_quest(self):
        task = MissionTask.__new__(MissionTask)
        task.cfg = {"badge_threshold": 0.85}
        task.t = {"ok_badge": "ok.png"}
        task.regions = {"list": None}
        clicks = []
        task.bot = types.SimpleNamespace(
            window=types.SimpleNamespace(frame_w=1920, frame_h=1080),
            matcher=types.SimpleNamespace(find_all=lambda *_a, **_k: []))
        task._grab = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
        task._click_xy = lambda label, *_a: clicks.append(label)
        task._find = lambda *_a, **_k: types.SimpleNamespace(x=1700, y=980)

        self.assertFalse(task._try_tab("서사", 331, safe_only=True))
        self.assertEqual(clicks, ["서사 탭"])

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

        picker_rows = task._daily_rows(
            np.zeros((1080, 1920, 3), dtype=np.uint8), include_meditation=True)
        self.assertEqual([text for text, _y in picker_rows], [
            "[일일] 낙화의 전장 요귀 처치",
            "[일일] 일일 명상 수련하기",
            "[일일] 비경 탐험 참여",
        ])

    def test_checked_daily_filter_tolerates_ocr_spacing(self):
        task = MissionTask.__new__(MissionTask)
        task.set_selected_daily_titles(["[일일] 산신의 영역 지하동굴 요귀 처치"])
        self.assertTrue(task._is_selected_daily("일일 산신의영역 지하 동굴 요귀처치"))
        self.assertFalse(task._is_selected_daily("[일일] 비경 탐험 참여"))

    def test_daily_guide_uses_safest_matching_region(self):
        task = MissionTask.__new__(MissionTask)
        task.cfg = {"daily_guide_fallback": True}
        spot = task._daily_guide_spot("[일일] 안개 기슭 요귀 처치")
        self.assertEqual(spot["name"], "안개 기슭 1")
        self.assertEqual(spot["power"], 37000)

    def test_sansin_cave_daily_prefers_free_guide_portal(self):
        task = MissionTask.__new__(MissionTask)
        task.cfg = {"daily_guide_fallback": True}
        title = "[일일] 산신의 영역 지하동굴 요귀 처치"
        spot = task._daily_guide_spot(title)
        self.assertEqual(spot["name"], "산신의 영역 지하 동굴 1")
        self.assertEqual(spot["power"], 94500)
        self.assertTrue(task._prefer_direct_guide(title))

    def test_daily_guide_does_not_route_unknown_event(self):
        task = MissionTask.__new__(MissionTask)
        task.cfg = {"daily_guide_fallback": True}
        self.assertIsNone(task._daily_guide_spot("[일일] 낙화의 전장 요귀 처치"))

    @patch("bot2.startup.read_map_name", return_value="[비경] 망자의 마을 3단계")
    @patch("bot2.farm._ocr", return_value="비경")
    @patch("tools.ocr.read_text")
    @patch("bot2.mission.time.sleep")
    def test_daily_secret_realm_selects_mangja_village_stage_three(
            self, _sleep, read_text, _ocr, _map_name):
        read_text.side_effect = [
            [{"text": "망자의 마을", "x": 100, "y": 200, "w": 120, "h": 30}],
            [{"text": "3 단계", "x": 500, "y": 300, "w": 80, "h": 30}],
        ]
        task = MissionTask.__new__(MissionTask)
        task.bot = MagicMock()
        task.bot.window.frame_w = 1920
        task.bot.window.frame_h = 1080
        task._grab = MagicMock(return_value=np.zeros((1080, 1920, 3), dtype=np.uint8))
        task._click_xy = MagicMock()

        self.assertTrue(task._enter_daily_secret_realm())
        labels = [call.args[0] for call in task._click_xy.call_args_list]
        self.assertEqual(labels[:2], ["비경 망자의 마을 선택", "망자의 마을 3단계 선택"])

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

    @patch("bot2.farm._ocr", side_effect=["완료 가능 개수 5/50", "완료 가능 개수 5/5"])
    def test_daily_quota_uses_binarized_retry_when_toggle_zero_is_attached(self, _ocr):
        task = MissionTask.__new__(MissionTask)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertTrue(task._daily_quota_full(frame))

    @patch("bot2.farm._ocr", return_value="임무 완료 가능 개수 5/5")
    def test_daily_five_of_five_means_all_five_are_already_accepted(self, _ocr):
        task = MissionTask.__new__(MissionTask)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertTrue(task._daily_quota_full(frame))

    @patch("bot2.farm._ocr", return_value="임무 완료 가능 개수 0/5")
    def test_daily_zero_of_five_allows_auto_acceptance(self, _ocr):
        task = MissionTask.__new__(MissionTask)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertFalse(task._daily_quota_full(frame))

    def test_selected_daily_intake_is_not_reopened_until_current_finishes(self):
        task = self.make_task(["일일", "본편", "서사", "주간"])
        task.selected_daily_titles = ["[일일] 비경 탐험 참여"]
        task.last_daily_title = "[일일] 비경 탐험 참여"
        task._daily_intake_checked = True
        self.assertEqual(task.ordered_tabs("일일", only=True), [])
        task._daily_intake_checked = False
        task.last_daily_title = ""
        self.assertEqual(task.ordered_tabs("일일", only=True), [("일일", 520)])

    def test_daily_accepted_badge_uses_right_speech_icon(self):
        task = MissionTask.__new__(MissionTask)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertFalse(task._daily_row_accepted(frame, 260))
        frame[240:280, 1730:1800] = 220
        self.assertTrue(task._daily_row_accepted(frame, 260))

    def test_daily_empty_checkbox_is_not_accepted_badge(self):
        task = MissionTask.__new__(MissionTask)
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        frame[240:280, 1830:1880] = 220
        self.assertFalse(task._daily_row_accepted(frame, 260))

    def test_selected_daily_marks_batch_accept_pending(self):
        task = MissionTask.__new__(MissionTask)
        task.set_selected_daily_titles(["[일일] 황무지 마을 요귀 처치"])
        self.assertTrue(task._daily_batch_pending)

    def test_checked_daily_quests_are_batch_accepted_before_progress(self):
        task = MissionTask.__new__(MissionTask)
        first = "[일일] 비경 탐험 참여"
        second = "[일일] 황무지 마을 요귀 처치"
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        active = {"title": "", "accepted": {first}}
        clicks = []
        task.selected_daily_titles = [first, second]
        task.cfg = {"badge_threshold": 0.85}
        task.t = {"ok_badge": "ok.png"}
        task.regions = {}
        task.bot = types.SimpleNamespace(
            window=types.SimpleNamespace(frame_w=1920),
            matcher=types.SimpleNamespace(find_all=lambda *_a, **_k: [types.SimpleNamespace(y=400)]))
        task._close = lambda: None
        task._stop_autohunt = lambda: None
        task._reopen_daily = lambda: True
        task._grab = lambda: frame
        task._daily_rows = lambda _frame, include_meditation=False: [(first, 300), (second, 400)]
        task._daily_row_accepted = lambda _frame, _y: False
        task._daily_quota_full = lambda _frame: False
        task._daily_limit_popup = lambda _frame: False
        task._click_xy = lambda label, *_a: (
            clicks.append(label),
            active.update(title=first if first in label else second if second in label else active["title"]),
            active["accepted"].add(active["title"]) if label.startswith("일일 일괄 수락:") else None)
        task._find = lambda _frame, key: (
            types.SimpleNamespace(x=1700, y=980)
            if ((key == "auto" and active["title"] in active["accepted"])
                or (key == "accept" and active["title"] not in active["accepted"])) else None)
        task._daily_intake_checked = True
        task.last_daily_title = first

        result = task.accept_selected_daily_batch()

        self.assertEqual(result["already"], 1)
        self.assertEqual(result["accepted"], 1)
        self.assertTrue(any(label.startswith("일일 일괄 수락:") for label in clicks))
        self.assertNotIn("자동 진행", clicks)
        self.assertFalse(task._daily_intake_checked)
        self.assertEqual(task.last_daily_title, "")

    def test_quota_full_still_opens_checked_row_and_verifies_auto_button(self):
        task = MissionTask.__new__(MissionTask)
        title = "[일일] 안개 기슭 요귀 처치"
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        clicks = []
        task.cfg = {"badge_threshold": 0.85}
        task.t = {"ok_badge": "ok.png"}
        task.regions = {}
        task.selected_daily_titles = [title]
        task.bot = types.SimpleNamespace(
            window=types.SimpleNamespace(frame_w=1920),
            matcher=types.SimpleNamespace(find_all=lambda *_a, **_k: []))
        task._close = lambda: None
        task._stop_autohunt = lambda: None
        task._reopen_daily = lambda: True
        task._grab = lambda: frame
        task._daily_quota_full = lambda _frame: True
        task._remember_daily_quota_done = lambda: None
        task._daily_rows = lambda _frame, include_meditation=False: [(title, 300)]
        task._is_selected_daily = lambda _title: True
        task._daily_row_accepted = lambda _frame, _y: False
        task._click_xy = lambda label, *_a: clicks.append(label)
        task._find = lambda _frame, key: (types.SimpleNamespace(x=1700, y=980)
                                          if key == "auto" else None)
        task._prefer_direct_guide = lambda _title: False
        task._on_screen = lambda: False
        task._progressing = lambda: True

        self.assertTrue(task._try_daily())
        self.assertIn("일일 후보 선택: " + title, clicks)
        self.assertIn("자동 진행", clicks)

    def test_completed_daily_is_not_reopened_but_other_tabs_remain(self):
        task = self.make_task(["일일", "본편", "서사", "주간"])
        task._daily_quota_done_day = date.today()
        self.assertEqual(task.ordered_tabs("일일", only=True), [])
        self.assertEqual([name for name, _x in task.ordered_tabs()], ["본편", "서사", "주간"])

    def test_completion_crosscheck_forces_daily_tab_after_quota_is_full(self):
        task = self.make_task(["일일", "본편", "서사", "주간"])
        task._daily_quota_done_day = date.today()
        task._daily_intake_checked = True
        self.assertEqual(task.ordered_tabs("일일", only=True, force_daily=True),
                         [("일일", 520)])

    def test_checked_accepted_daily_is_still_opened_after_quota_is_full(self):
        task = self.make_task(["일일", "본편", "서사", "주간"])
        task._daily_quota_done_day = date.today()
        task.selected_daily_titles = ["[일일] 비경 탐험 참여"]
        self.assertEqual(task.ordered_tabs("일일", only=True), [("일일", 520)])

    def test_daily_completion_survives_restart(self):
        from bot2 import rewards
        old = rewards.STATE_FILE
        with tempfile.TemporaryDirectory() as directory:
            rewards.STATE_FILE = os.path.join(directory, "state.json")
            try:
                first = MissionTask.__new__(MissionTask)
                first._remember_daily_quota_done()
                second = MissionTask.__new__(MissionTask)
                self.assertEqual(second._load_daily_quota_done_day(), date.today())
            finally:
                rewards.STATE_FILE = old


if __name__ == "__main__":
    unittest.main()
