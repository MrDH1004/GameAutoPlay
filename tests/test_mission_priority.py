import unittest

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


if __name__ == "__main__":
    unittest.main()
