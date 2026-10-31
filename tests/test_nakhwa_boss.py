"""22시 요화선녀 낙화의 전장 예약 흐름."""
import types
import unittest
from datetime import datetime
import os
import tempfile
from unittest.mock import patch

import numpy as np

from bot2.brain import Bot
from bot2.nakhwa_boss import NakhwaBossTask
from bot2.mission import MissionTask
from bot2.preset import PRESET_ROWS, PresetSwitcher
from bot2 import safety
from tests.test_logic import config, img, ROOT


class PresetRangeTest(unittest.TestCase):
    def test_all_user_presets_a_to_e_have_click_rows(self):
        self.assertEqual(sorted(PRESET_ROWS), [1, 2, 3, 4, 5])
        self.assertEqual(PRESET_ROWS[2], (1558, 867))
        self.assertEqual(PRESET_ROWS[4], (1558, 739))
        self.assertEqual(PRESET_ROWS[5], (1558, 675))

    def test_switch_uses_h_quick_menu_without_opening_skill_editor(self):
        calls = []
        switcher = object.__new__(PresetSwitcher)
        switcher.drv = types.SimpleNamespace(
            key=lambda key: calls.append(("key", key)),
            sleep=lambda seconds: calls.append(("sleep", seconds)),
            click=lambda x, y: calls.append(("click", x, y)),
        )
        switcher.boss = 2
        switcher.current = 2
        self.assertTrue(switcher.switch(4))
        self.assertEqual(calls[0], ("key", "h"))
        self.assertIn(("click", *PRESET_ROWS[4]), calls)
        self.assertNotIn(("key", "k"), calls)


class FakeMission:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def _enter_nakhwa_battlefield(self, reason="일일"):
        self.calls.append(reason)
        return self.result

    def recover_nakhwa_center(self, reason="부활"):
        self.calls.append(reason)
        return self.result


class FakeBot:
    def __init__(self, result=True):
        self.mission = FakeMission(result)
        self._map_name = "황무지 마을"
        self._source = "본편"
        self._want_hunt = False
        self.saved = []

    def _save_screen(self, _frame, prefix):
        self.saved.append(prefix)


def snap(in_field=True):
    return types.SimpleNamespace(in_field=in_field, frame=np.zeros((1080, 1920, 3), np.uint8))


class NakhwaBossTaskTest(unittest.TestCase):
    def test_nakhwa_uses_configured_map_hunt_points(self):
        cfg = {"center_move_target": [0.504, 0.509], "boss_move_target": [0.504, 0.509]}
        self.assertEqual(MissionTask._nakhwa_move_target(cfg, "일일"), [0.504, 0.509])
        self.assertEqual(MissionTask._nakhwa_move_target(cfg, "요화선녀 위치 재확인"),
                         [0.504, 0.509])
        self.assertEqual(MissionTask._nakhwa_move_target(cfg, "거점 부활", "낙화 요화선녀"),
                         [0.504, 0.509])

    def test_recovery_moves_to_hunt_point_opens_scoreboard_then_selects_preset_b(self):
        calls = []

        class Switcher:
            def switch(self, number):
                calls.append(("preset", number))
                return True

        mission = object.__new__(MissionTask)
        mission.bot = types.SimpleNamespace(
            cfg={"nakhwa_boss": {
                "stay_at_current_position": False, "scoreboard_tab": [0.974, 0.114],
                "center_move_target": [0.504, 0.509], "hunt_preset": 2,
                "auto_button": [0.887, 0.942],
            }},
            _source="일일", preset=Switcher(), _nakhwa_scoreboard_opened=False,
            window=types.SimpleNamespace(frame_w=1920, frame_h=1080),
            eyes=types.SimpleNamespace(look=lambda *_args: types.SimpleNamespace(auto_on=len(calls) >= 4)),
        )
        mission._grab = lambda: np.zeros((1080, 1920, 3), np.uint8)
        mission._walk_nakhwa_map_target = lambda target, _cfg: calls.append(("walk", target)) or True
        mission._click_xy = lambda label, *_args: calls.append(("click", label))

        self.assertTrue(mission.recover_nakhwa_center("일일"))
        self.assertEqual(calls[0], ("walk", [0.504, 0.509]))
        self.assertIn("낙화 전용 임무 점수표", calls[1][1])
        self.assertEqual(calls[2], ("preset", 2))
        self.assertIn("낙화 AUTO 버튼", calls[3][1])

    def test_daily_2158_to_2212_window(self):
        task = NakhwaBossTask(FakeBot(), {})
        self.assertFalse(task.due(datetime(2026, 10, 20, 21, 57, 59)))
        self.assertTrue(task.due(datetime(2026, 10, 20, 21, 58, 0)))
        self.assertTrue(task.due(datetime(2026, 10, 20, 22, 11, 59)))
        self.assertFalse(task.due(datetime(2026, 10, 20, 22, 12, 0)))

    def test_enters_once_and_uses_boss_reason(self):
        bot = FakeBot()
        task = NakhwaBossTask(bot, {})
        wall = datetime(2026, 10, 20, 21, 58, 0)
        self.assertTrue(task.tick(snap(), 10.0, wall))
        self.assertEqual(bot.mission.calls, ["요화선녀"])
        self.assertEqual(task.attempted_date, wall.date())
        self.assertEqual(bot._source, "낙화 요화선녀")
        self.assertFalse(task.tick(snap(), 11.0, wall))

    def test_failed_entry_retries_after_delay(self):
        bot = FakeBot(False)
        task = NakhwaBossTask(bot, {"retry_seconds": 15})
        wall = datetime(2026, 10, 20, 22, 0, 0)
        self.assertTrue(task.tick(snap(), 10.0, wall))
        self.assertFalse(task.tick(snap(), 24.9, wall))
        self.assertTrue(task.tick(snap(), 25.0, wall))
        self.assertEqual(bot.mission.calls, ["요화선녀", "요화선녀"])
        self.assertEqual(bot.saved, ["nakhwa_boss_entry_failure"] * 2)

    def test_missing_mission_does_not_consume_daily_attempt(self):
        bot = FakeBot()
        bot.mission = None
        task = NakhwaBossTask(bot, {"retry_seconds": 15})
        wall = datetime(2026, 10, 20, 21, 58, 0)

        self.assertTrue(task.tick(snap(), 10.0, wall))

        self.assertIsNone(task.attempted_date)
        self.assertEqual(task.phase, "retry")
        self.assertEqual(task.next_try, 25.0)

    def test_already_inside_rechecks_center_and_auto(self):
        bot = FakeBot()
        bot._map_name = "낙화의 전장"
        task = NakhwaBossTask(bot, {})
        wall = datetime(2026, 10, 20, 22, 0, 0)
        self.assertTrue(task.tick(snap(), 10.0, wall))
        self.assertFalse(bot._want_hunt)
        self.assertEqual(bot._source, "낙화 요화선녀")
        self.assertEqual(bot.mission.calls, ["요화선녀 위치 재확인"])

    def test_death_uses_base_revive_and_stops_boss_reentry(self):
        death_log = os.path.join(ROOT, "logs", "_test_nakhwa_deaths.jsonl")
        safety.DEATH_LOG = death_log
        bot = Bot(config(), dry_run=True)
        bot._save_screen = lambda _frame, _prefix: None
        bot._map_name = "낙화의 전장"
        bot._source = "낙화 요화선녀"
        retired = []
        bot.nakhwa_boss.retire_after_death = lambda: retired.append(True)
        bot._on_dead(types.SimpleNamespace(frame=img("death_base_revive.png"), state="DEAD"), 100)
        self.assertIsNone(bot._nakhwa_recenter_pending)
        self.assertIsNone(bot._retry_quest)
        self.assertFalse(bot._want_hunt)
        self.assertEqual(retired, [True])
        if os.path.exists(death_log):
            os.remove(death_log)

    def test_death_retirement_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            state_file = os.path.join(folder, "state.json")
            wall = datetime(2026, 10, 20, 22, 1, 0)
            with patch("bot2.rewards.STATE_FILE", state_file):
                first = NakhwaBossTask(FakeBot(), {})
                first.retire_after_death(wall)
                restarted = NakhwaBossTask(FakeBot(), {})
                self.assertTrue(restarted.retired_today(wall))
                self.assertTrue(restarted.retired_during_event(wall))
                self.assertFalse(restarted.due(wall))

    def test_center_return_runs_after_field_is_visible(self):
        calls = []
        bot = types.SimpleNamespace(
            _nakhwa_recenter_pending={"at": 106.0, "tries": 0},
            mission=types.SimpleNamespace(recover_nakhwa_center=lambda reason: calls.append(reason) or True),
            _want_hunt=True, _hunt_tries=2, _hunt_press_at=99.0)
        self.assertTrue(Bot._recover_nakhwa_after_revive(
            bot, types.SimpleNamespace(in_field=False), 106.0))
        self.assertEqual(calls, [])
        self.assertTrue(Bot._recover_nakhwa_after_revive(
            bot, types.SimpleNamespace(in_field=True), 107.0))
        self.assertEqual(calls, ["거점 부활"])
        self.assertIsNone(bot._nakhwa_recenter_pending)
        self.assertFalse(bot._want_hunt)


if __name__ == "__main__":
    unittest.main()
