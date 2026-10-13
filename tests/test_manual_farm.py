"""수동 앵벌 모드: 요청을 봇 루프에서 처리해 farm_mode를 켜고/꺼서 설정값으로 되돌리는지."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot2.brain import Bot  # noqa: E402


def make(cfg_farm=False):
    b = types.SimpleNamespace()
    b.cfg = {}
    b.manual_farm = False
    b._manual_farm_request = None
    b._cfg_farm_mode = cfg_farm
    b.farm_mode = cfg_farm
    b._retry_quest = "x"
    b._need_switch = True
    b.farm = types.SimpleNamespace(need_travel=False, next_try=9, next_eval=9)
    stopped = []
    b.collect = types.SimpleNamespace(stop=lambda: stopped.append(1))
    b.stopped = stopped
    b._collect_pending = False
    b._want_hunt = False
    b._hunt_tries = 3
    b._hunt_press_at = 5
    b._farm_until = 100.0
    b._fails = 4
    b._click_at = 7.0
    b._idle_since = 1.0
    b._verify = "v"
    b.notifier = types.SimpleNamespace(send=lambda *a, **k: None)
    b.focused = []
    b._focus_game_window = lambda: b.focused.append(1)
    b.request_manual_farm = types.MethodType(Bot.request_manual_farm, b)
    b.apply = types.MethodType(Bot._apply_manual_farm, b)
    return b


class ManualFarmTest(unittest.TestCase):
    def test_on_then_off(self):
        b = make()
        b.apply(0)                                   # 요청 없음 → 아무 일 없음
        self.assertFalse(b.farm_mode)
        b.request_manual_farm(True)
        b.apply(1)
        self.assertTrue(b.manual_farm and b.farm_mode)
        self.assertTrue(b.farm.need_travel and b._collect_pending and b._want_hunt)
        self.assertIsNone(b._retry_quest)
        self.assertEqual(b.focused, [1])                              # 켤 때 게임 창을 앞으로
        b.request_manual_farm(False)
        b.apply(2)
        self.assertFalse(b.manual_farm or b.farm_mode)                # 설정값(False)으로 복귀
        self.assertEqual((b._farm_until, b._fails, b._click_at), (0.0, 0, 0.0))
        self.assertEqual(b.stopped, [1])

    def test_off_keeps_config_farm_mode(self):
        b = make(cfg_farm=True)
        b.request_manual_farm(True)
        b.apply(1)
        b.request_manual_farm(False)
        b.apply(2)
        self.assertTrue(b.farm_mode)                                  # 설정이 앵벌 전용이면 계속 앵벌


if __name__ == "__main__":
    unittest.main()
