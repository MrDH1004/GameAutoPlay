"""시간제한 비경에서는 일반 자동사냥만 유지하고, 일반 맵 복귀 뒤 앵벌을 재개한다."""
import types
import unittest

from bot2.brain import Bot
from bot2.startup import is_hunt_only_content_name, is_secret_realm_name


def make_bot():
    farm = types.SimpleNamespace(need_travel=False, next_try=99.0, next_eval=99.0)
    bot = types.SimpleNamespace(
        _map_name="", _map_observed_at=-1.0, _secret_realm=False, _secret_realm_base="",
        _normal_map_candidate="", _normal_map_confirm=0,
        _rest="finish", _evade={"old": True}, _evade_pending=True, _evade_request=True,
        _want_hunt=False, _hunt_tries=2, _hunt_press_at=3.0, _hunt_check=0.0,
        _last_progress=0.0, _idle_since=1.0, _verify={"ok": False},
        farm=farm, farm_mode=True, _farm_until=0.0,
        risk_guard=types.SimpleNamespace(arm=lambda *_: None),
    )
    bot.observe = types.MethodType(Bot._observe_map, bot)
    return bot


class SecretRealmNameTest(unittest.TestCase):
    def test_brackets_and_spaces_are_optional(self):
        self.assertTrue(is_secret_realm_name("[비경] 영험한 청산 지하동굴"))
        self.assertTrue(is_secret_realm_name("비 경] 영험한청산지하동굴"))
        self.assertTrue(is_secret_realm_name("[B.트!JP험한 청산 지하동"))
        self.assertFalse(is_secret_realm_name("산신의 영역 지하 동굴"))
        self.assertFalse(is_secret_realm_name(""))

    def test_nakhwa_battlefield_is_hunt_only_content(self):
        self.assertTrue(is_hunt_only_content_name("낙화의 전장"))
        self.assertTrue(is_hunt_only_content_name("낙화의전장"))
        self.assertTrue(is_hunt_only_content_name("낙화의』전장,囹-"))
        self.assertFalse(is_hunt_only_content_name("안개 기슭"))


class SecretRealmStateTest(unittest.TestCase):
    def test_nakhwa_battlefield_uses_same_input_lock(self):
        bot = make_bot()
        self.assertTrue(bot.observe("낙화의 전장", 1.0))
        self.assertTrue(bot._secret_realm)
        self.assertTrue(bot._want_hunt)
        self.assertTrue(bot.farm.need_travel)

    def test_enter_immediately_cancels_movement_actions(self):
        bot = make_bot()
        self.assertTrue(bot.observe("[비경] 영험한청산지하동굴", 1.0))
        self.assertTrue(bot._secret_realm)
        self.assertIsNone(bot._rest)
        self.assertIsNone(bot._evade)
        self.assertFalse(bot._evade_pending)
        self.assertFalse(bot._evade_request)
        self.assertTrue(bot._want_hunt)
        self.assertTrue(bot.farm.need_travel)       # 실행하지 않고 종료 뒤까지 보류

    def test_exit_requires_same_normal_map_on_two_frames_then_restarts_farm(self):
        bot = make_bot()
        bot.observe("[비경] 영험한청산지하동굴", 1.0)
        self.assertTrue(bot.observe("산신의 영역 지하 동굴", 2.0))
        self.assertTrue(bot._secret_realm)
        self.assertFalse(bot.observe("산신의 영역 지하 동굴", 3.0))
        self.assertFalse(bot._secret_realm)
        self.assertTrue(bot.farm.need_travel)
        self.assertEqual(bot.farm.next_try, 0.0)
        self.assertEqual(bot.farm.next_eval, 0.0)

    def test_one_ocr_misread_does_not_release_lock(self):
        bot = make_bot()
        bot.observe("[비경] 영험한청산지하동굴", 1.0)
        bot.observe("영험한청산지하동굴", 2.0)       # [비경] 접두어가 연속으로 누락돼도 유지
        bot.observe("영험한청산지하동굴", 3.0)
        self.assertTrue(bot._secret_realm)
        self.assertEqual(bot._normal_map_confirm, 0)

    def test_secret_realm_handler_only_ensures_hunt(self):
        bot = make_bot()
        calls = []
        bot._ensure_hunt = lambda snap, now: calls.append((snap.auto_on, now))
        snap = types.SimpleNamespace(auto_on=False, walking=False)
        Bot._on_secret_realm(bot, snap, 6.0)
        self.assertEqual(calls, [(False, 6.0)])
        self.assertTrue(bot._want_hunt)
        self.assertIsNone(bot._verify)

    def test_secret_realm_retries_hunt_instead_of_pausing(self):
        bot = make_bot()
        bot._hunt_tries = 3
        bot._hunt_press_at = 1.0
        seen = []
        bot._ensure_hunt = lambda _snap, _now: seen.append(bot._hunt_tries)
        snap = types.SimpleNamespace(auto_on=False, walking=False)
        Bot._on_secret_realm(bot, snap, 5.0)
        self.assertEqual(seen, [0])


if __name__ == "__main__":
    unittest.main()
