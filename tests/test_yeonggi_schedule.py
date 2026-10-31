"""영기 수련 남은 시간 저장·재시작 복원 검증."""
import types
import unittest
from datetime import datetime
from unittest.mock import patch

from bot.yeonggi import YeonggiTask


CFG = {"interval": 180, "templates": {}, "regions": {}}


class YeonggiScheduleTest(unittest.TestCase):
    def test_future_schedule_is_restored_after_restart(self):
        now_wall = 2_000_000_000
        future = datetime.fromtimestamp(now_wall + 600).isoformat(timespec="seconds")
        state = {"yeonggi_schedule": {"checked_at": datetime.fromtimestamp(now_wall - 100).isoformat(),
                                       "remaining_seconds": 685,
                                       "next_check_at": future}}
        with patch("bot2.rewards._load_state", return_value=state), \
                patch("bot.yeonggi.time.time", return_value=now_wall), \
                patch("bot.yeonggi.time.monotonic", return_value=50):
            task = YeonggiTask(types.SimpleNamespace(), CFG)
            self.assertEqual(task.next_check, 650)
            self.assertFalse(task.due(649))

    def test_remaining_time_is_saved_and_blocks_early_check(self):
        saved = {}
        with patch("bot2.rewards._load_state", return_value={}), \
                patch("bot2.rewards._save_state", side_effect=lambda value: saved.update(value)), \
                patch("bot.yeonggi.time.time", return_value=1000), \
                patch("bot.yeonggi.time.monotonic", return_value=50):
            task = YeonggiTask(types.SimpleNamespace(), CFG)
            wait = task._schedule_from_remaining(600)
            self.assertEqual(wait, 615)
            self.assertEqual(saved["yeonggi_schedule"]["remaining_seconds"], 600)
            self.assertFalse(task.due(664))
            self.assertFalse(task.due(665))  # 벽시계도 예정 시각 전이므로 차단

    def test_five_minute_clan_help_schedule_is_saved(self):
        saved = {}
        cfg = {**CFG, "clan_help_interval": 300}
        with patch("bot2.rewards._load_state", return_value={}), \
                patch("bot2.rewards._save_state", side_effect=lambda value: saved.update(value)), \
                patch("bot.yeonggi.time.time", return_value=1000), \
                patch("bot.yeonggi.time.monotonic", return_value=50):
            task = YeonggiTask(types.SimpleNamespace(), cfg)
            task._schedule_clan_help(300)
            self.assertIn("clan_help_next_at", saved["yeonggi_schedule"])
            self.assertFalse(task._clan_help_due(349))

    def test_old_hourly_schedule_is_clamped_to_five_minutes(self):
        now_wall = 2_000_000_000
        future = datetime.fromtimestamp(now_wall + 3600).isoformat(timespec="seconds")
        state = {"yeonggi_schedule": {"clan_help_next_at": future}}
        cfg = {**CFG, "clan_help_interval": 300}
        with patch("bot2.rewards._load_state", return_value=state), \
                patch("bot.yeonggi.time.time", return_value=now_wall), \
                patch("bot.yeonggi.time.monotonic", return_value=50):
            task = YeonggiTask(types.SimpleNamespace(), cfg)
        self.assertEqual(task.next_clan_help, 350)

    def test_clan_help_enters_current_training_before_click(self):
        cfg = {**CFG, "clan_help_interval": 300,
               "templates": {"current_go": "go.png", "clan_help": "help.png"}}
        with patch("bot2.rewards._load_state", return_value={}):
            task = YeonggiTask(types.SimpleNamespace(), cfg)
        go = types.SimpleNamespace(x=1180, y=990)
        help_button = types.SimpleNamespace(x=720, y=820)
        calls = []
        def find(frame, key, threshold=None):
            if (frame, key) == ("tree", "current_go"):
                return go
            # 첫 화면에 다른 문파 지원 아이콘이 보여도 바로가기가 우선이어야 한다.
            if (frame, key) == ("tree", "clan_help"):
                return help_button
            return None
        task._find = find
        task._wait_for = lambda key, timeout=5.0: ("detail", help_button)
        task._click = lambda label, match, pause=1.0: calls.append(label)
        task._schedule_clan_help = lambda wait: calls.append(("schedule", wait))

        self.assertTrue(task._clan_help("tree", 100))
        self.assertEqual(calls, ["진행 중인 수련 바로가기", "영기 수련 문파 도움",
                                 ("schedule", 300.0)])


if __name__ == "__main__":
    unittest.main()
