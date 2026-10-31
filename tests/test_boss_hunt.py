"""21시 보스토벌 예약 상태 흐름과 유료 이동 예외 범위를 검증한다."""
import types
import unittest
from datetime import datetime
from unittest import mock

import numpy as np

from bot2.boss_hunt import BossHuntTask


class FakeBot:
    def __init__(self):
        self.calls = []
        self.matcher = types.SimpleNamespace(find=lambda *_a, **_k: None)
        self.preset = None
        self.farm = types.SimpleNamespace(need_travel=False, next_try=99.0, next_eval=99.0)
        self.farm_mode = True
        self._farm_until = 0.0
        self._source = "본편"
        self._want_hunt = False
        self._hunt_tries = 0
        self._hunt_press_at = 0.0

    def _press(self, label, key):
        self.calls.append(("key", label, key))

    def _click_frame(self, label, x, y):
        self.calls.append(("click", label, x, y))


def snap(auto_on=False, in_field=True, state="IDLE"):
    return types.SimpleNamespace(auto_on=auto_on, in_field=in_field, state=state,
                                 frame=np.zeros((1080, 1920, 3), np.uint8))


class BossHuntScheduleTest(unittest.TestCase):
    def test_daily_target_rotation(self):
        expected = ["산신 산군대호", "해골장군", "산신 산군대호", "해골장군",
                    "산신 산군대호", "해골장군", "산신 산군대호"]
        self.assertEqual([BossHuntTask.target_for(day)[0] for day in range(7)], expected)

    def test_prepare_window(self):
        task = BossHuntTask(FakeBot(), {})
        self.assertFalse(task.due(datetime(2026, 10, 16, 20, 54, 59)))
        self.assertTrue(task.due(datetime(2026, 10, 16, 20, 55, 0)))
        self.assertTrue(task.due(datetime(2026, 10, 16, 21, 4, 59)))
        self.assertFalse(task.due(datetime(2026, 10, 16, 21, 5, 0)))

    def test_each_boss_can_be_disabled_independently(self):
        tiger_off = BossHuntTask(FakeBot(), {"mountain_tiger_enabled": False})
        skeleton_off = BossHuntTask(FakeBot(), {"skeleton_general_enabled": False})
        self.assertFalse(tiger_off.due(datetime(2026, 10, 19, 20, 55)))  # 월: 산군대호
        self.assertTrue(tiger_off.due(datetime(2026, 10, 20, 20, 55)))   # 화: 해골장군
        self.assertTrue(skeleton_off.due(datetime(2026, 10, 19, 20, 55)))
        self.assertFalse(skeleton_off.due(datetime(2026, 10, 20, 20, 55)))

    def test_normal_mode_saves_return_position_before_boss(self):
        bot = FakeBot()
        bot.normal_mode = True
        saved = []
        bot._ensure_normal_origin = lambda: saved.append(True) or True
        task = BossHuntTask(bot, {})
        self.assertTrue(task.tick(snap(auto_on=True), 0.0, datetime(2026, 10, 16, 20, 58, 0)))
        self.assertEqual(saved, [True])


class BossHuntFlowTest(unittest.TestCase):
    def test_map_evidence_tolerates_spacing_punctuation_and_one_ocr_error(self):
        task = BossHuntTask(FakeBot(), {})
        task.target_name = "해골장군"
        with mock.patch("bot2.boss_hunt._ocr", side_effect=[
                "목적지 해골장군", "축지슐 2,000", "2,000"]):
            evidence = task._map_evidence(snap().frame)
        self.assertEqual((evidence["target"], evidence["travel"], evidence["price"]),
                         (True, True, True))

    def test_map_evidence_accepts_any_variable_price(self):
        task = BossHuntTask(FakeBot(), {})
        task.target_name = "산신 산군대호"
        with mock.patch("bot2.boss_hunt._ocr", side_effect=[
                "산신 산군대호", "977m 걸어가기 5,000 축지술", "5,000"]):
            allowed = task._map_evidence(snap().frame)
        with mock.patch("bot2.boss_hunt._ocr", side_effect=[
                "산신 산군대호", "977m 걸어가기 7,000 축지술", "7,000"]):
            blocked = task._map_evidence(snap().frame)
        self.assertTrue(allowed["price"])
        self.assertEqual(allowed["observed_price"], 5000)
        self.assertTrue(blocked["price"])

    def test_world_map_resyncs_when_event_menu_remains_open(self):
        bot = FakeBot()
        task = BossHuntTask(bot, {})
        task.phase = "world_map"
        task.phase_at = 10.0
        task.target_name = "해골장군"
        task._event_open = lambda _f: True
        task._map_target_ready = lambda _f: False
        task._at_boss_field = lambda _f: False

        task.tick(snap(in_field=False, state="OVERLAY"), 11.0,
                  datetime(2026, 10, 20, 19, 59))

        self.assertEqual(task.phase, "event_menu")
        self.assertTrue(any(c[0] == "click" and c[1] == "해골장군 선택" for c in bot.calls))

    def test_one_flow_walks_early_then_restores_farm(self):
        bot = FakeBot()
        task = BossHuntTask(bot, {})
        task._event_icon = lambda _f: types.SimpleNamespace(x=50, y=904)
        task._event_open = lambda _f: True
        task._map_target_ready = lambda _f: True
        task._walk_target_ready = lambda _f: True
        task._at_boss_field = lambda _f: True
        boss = {"visible": False}
        task._boss_visible = lambda _s: boss["visible"]
        before = datetime(2026, 10, 16, 20, 58, 0)
        start = datetime(2026, 10, 16, 21, 0, 0)

        task.tick(snap(auto_on=True), 0.0, before)       # 기존 앵벌 T 끄기
        task.tick(snap(auto_on=False), 4.0, before)      # 아이콘
        task.tick(snap(auto_on=False, in_field=False, state="OVERLAY"), 5.1, before)  # 당일 카드
        task.tick(snap(auto_on=False, in_field=False, state="OVERLAY"), 7.1, before)  # 세계지도
        task.tick(snap(auto_on=False, in_field=False, state="OVERLAY"), 8.2, before)  # 검증된 걸어가기
        task.tick(snap(auto_on=False), 12.0, before)     # 도착
        task.tick(snap(auto_on=False), 13.0, start)      # 21:00 T 켜기
        boss["visible"] = True
        task.tick(snap(auto_on=True, state="COMBAT"), 16.0, start)
        boss["visible"] = False
        task.tick(snap(auto_on=True), 27.0, datetime(2026, 10, 16, 21, 1, 0))
        task.tick(snap(auto_on=True), 31.0, datetime(2026, 10, 16, 21, 1, 4))
        task.tick(snap(auto_on=False), 35.0, datetime(2026, 10, 16, 21, 1, 8))

        walked = [c for c in bot.calls if c[0] == "click" and "무료 걸어가기" in c[1]]
        self.assertEqual(len(walked), 1)
        self.assertEqual(walked[0][2:], BossHuntTask.WALK_TRAVEL)
        self.assertFalse(any(c[0] == "click" and "축지술" in c[1] for c in bot.calls))
        self.assertTrue(bot.farm.need_travel)
        self.assertEqual(bot.farm.next_try, 0.0)
        self.assertEqual(bot.farm.next_eval, 0.0)
        self.assertEqual(task.phase, "idle")
        self.assertEqual(bot._source, "본편")

    def test_wrong_map_never_clicks_paid_travel(self):
        bot = FakeBot()
        task = BossHuntTask(bot, {})
        task.phase = "world_map"
        task.phase_at = 0.0
        task.active_date = datetime(2026, 10, 16).date()
        task.target_name = "산신 산군대호"
        task._map_target_ready = lambda _f: False
        task.tick(snap(in_field=False, state="OVERLAY"), 26.0, datetime(2026, 10, 16, 21, 0))
        self.assertFalse(any(c[0] == "click" and "축지술" in c[1] for c in bot.calls))
        self.assertIn(("key", "보스토벌 화면 닫기", "m"), bot.calls)
        self.assertEqual(task.next_try, 56.0)
        self.assertIsNone(task.attempted_date)

    def test_failed_entry_retries_inside_event_window(self):
        task = BossHuntTask(FakeBot(), {"retry_seconds": 30})
        task.next_try = 100.0
        wall = datetime(2026, 10, 16, 20, 59)
        self.assertFalse(task.tick(snap(), 99.0, wall))
        self.assertTrue(task.tick(snap(auto_on=True), 100.0, wall))
        self.assertEqual(task.phase, "stop_hunt")

    def test_paid_travel_is_rechecked_on_fresh_frame(self):
        bot = FakeBot()
        bot.dry_run = False
        bot._check_user = lambda: bot.calls.append(("check",))
        bot.window = types.SimpleNamespace(grab=lambda: "fresh")
        task = BossHuntTask(bot, {})
        task.phase = "world_map"
        task.phase_at = 0.0
        task.target_name = "산신 산군대호"
        task._map_target_ready = lambda frame: not isinstance(frame, str)
        task.tick(snap(in_field=False, state="OVERLAY"), 1.0, datetime(2026, 10, 16, 21, 0))
        self.assertIn(("check",), bot.calls)
        self.assertFalse(any(c[0] == "click" and "축지술" in c[1] for c in bot.calls))
        self.assertEqual(task.phase, "world_map")

    def test_open_event_resyncs_when_event_screen_is_already_open(self):
        bot = FakeBot()
        task = BossHuntTask(bot, {})
        task.phase = "open_event"
        task.phase_at = 10.0
        task.target_name = "산신 산군대호"
        task._event_open = lambda _f: True
        task._map_target_ready = lambda _f: False
        task._at_boss_field = lambda _f: False

        task.tick(snap(in_field=False, state="OVERLAY"), 11.0,
                  datetime(2026, 10, 18, 20, 58))

        self.assertEqual(task.phase, "event_menu")
        self.assertFalse(any(c[0] == "click" and c[1] == "보스토벌 아이콘"
                             for c in bot.calls))

    def test_resyncs_to_world_map_after_manual_progress(self):
        bot = FakeBot()
        task = BossHuntTask(bot, {})
        task.phase = "event_menu"
        task.phase_at = 10.0
        task.target_name = "산신 산군대호"
        task._event_open = lambda _f: False
        task._map_target_ready = lambda _f: True
        task._at_boss_field = lambda _f: False

        task.tick(snap(in_field=False, state="OVERLAY"), 11.0,
                  datetime(2026, 10, 18, 21, 0))

        self.assertEqual(task.phase, "traveling")
        self.assertTrue(any(c[0] == "click" and "보스토벌 축지술" in c[1]
                            for c in bot.calls))

    def test_free_walk_switches_to_any_price_travel_at_start(self):
        bot = FakeBot()
        task = BossHuntTask(bot, {})
        task.phase = "traveling"
        task.phase_at = 10.0
        task.target_name = "산신 산군대호"
        task.free_walk_started = True
        task._at_boss_field = lambda _f: False

        task.tick(snap(in_field=True), 20.0, datetime(2026, 10, 18, 21, 0))
        self.assertEqual(task.phase, "open_event")

        task.phase = "world_map"
        task.phase_at = 21.0
        task._map_target_ready = lambda _f: True
        task.tick(snap(in_field=False, state="OVERLAY"), 22.0,
                  datetime(2026, 10, 18, 21, 0))
        self.assertTrue(task.paid_travel_started)
        self.assertTrue(any(c[0] == "click" and "보스토벌 축지술" in c[1]
                            for c in bot.calls))

    def test_resyncs_to_waiting_when_already_at_boss_field(self):
        bot = FakeBot()
        task = BossHuntTask(bot, {})
        task.phase = "world_map"
        task.phase_at = 10.0
        task.target_name = "산신 산군대호"
        task._at_boss_field = lambda _f: True

        task.tick(snap(in_field=True), 200.0, datetime(2026, 10, 18, 20, 59))

        self.assertEqual(task.phase, "waiting")

    def test_traveling_does_not_treat_region_waypoint_as_boss_field(self):
        task = BossHuntTask(FakeBot(), {"travel_timeout_seconds": 420})
        task.phase = "traveling"
        task.phase_at = 10.0
        task.target_name = "산신 산군대호"
        task._at_boss_field = lambda _f: False

        task.tick(snap(in_field=True), 100.0, datetime(2026, 10, 18, 20, 59))

        self.assertEqual(task.phase, "traveling")

    def test_user_wait_does_not_consume_phase_timeout(self):
        task = BossHuntTask(FakeBot(), {})
        task.phase = "event_menu"
        task.phase_at = 10.0
        task._input_at = 9.0

        task.on_user_resume(200.0)

        self.assertEqual(task.phase_at, 200.0)
        self.assertLess(task._input_at, 0.0)


if __name__ == "__main__":
    unittest.main()
