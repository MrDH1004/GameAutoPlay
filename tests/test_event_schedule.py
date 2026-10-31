import unittest
from datetime import datetime

from event_schedule import dashboard_schedule


class EventScheduleTest(unittest.TestCase):
    def test_monday_dashboard_contains_all_requested_events(self):
        cfg = {
            "boss_hunt": {"starts_at": "21:00", "duration_minutes": 5},
            "nakhwa_boss": {"starts_at": "22:00"},
            "event_calendar": {"nakhwa_depth": {
                "title": "낙화의 전장 · 심처 개방",
                "starts_at": "2026-10-17T23:00:00",
                "ends_at": "2026-10-18T23:00:00",
            }},
        }
        text = dashboard_schedule(datetime(2026, 10, 19, 12), cfg)
        self.assertIn("산신 산군대호", text)
        self.assertIn("21:00~21:05", text)
        self.assertIn("자동 참가", text)
        self.assertIn("요화선녀", text)
        self.assertIn("자동 입장·사냥", text)
        self.assertIn("심처 개방", text)
        self.assertIn("종료", text)

    def test_tuesday_rotates_to_skeleton_general(self):
        text = dashboard_schedule(datetime(2026, 10, 20), {})
        self.assertIn("해골장군", text)
        self.assertIn("21:00~21:05", text)


if __name__ == "__main__":
    unittest.main()
