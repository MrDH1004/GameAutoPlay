"""시간제한 비경에서는 일반 자동사냥만 유지하고, 일반 맵 복귀 뒤 앵벌을 재개한다."""
import types
import unittest
from datetime import datetime
from unittest import mock

from bot2.brain import Bot, _same_map_read
from bot2.startup import is_hunt_only_content_name, is_secret_realm_name


def make_bot():
    farm = types.SimpleNamespace(need_travel=False, next_try=99.0, next_eval=99.0,
                                 current={"id": "before-instance"})
    bot = types.SimpleNamespace(
        _map_name="", _map_observed_at=-1.0, _secret_realm=False, _secret_realm_base="",
        _normal_map_candidate="", _normal_map_confirm=0,
        _rest="finish", _evade={"old": True}, _evade_pending=True, _evade_request=True,
        _want_hunt=False, _hunt_tries=2, _hunt_press_at=3.0, _hunt_check=0.0,
        _last_progress=0.0, _idle_since=1.0, _verify={"ok": False},
        farm=farm, farm_mode=True, _farm_until=0.0,
        daily_mode=False, _source="", mission=None,
        secret_realm_combat_cfg={"daily_duration_minutes": 60},
        secret_realm_preset=None, _secret_preset_current=None,
        _secret_preset_switched_at=-1e9,
        _daily_secret_started_wall=0.0, _daily_secret_exit_pending=0.0,
        _nakhwa_daily_positioned=False, _nakhwa_check_at=0.0,
        risk_guard=types.SimpleNamespace(arm=lambda *_: None),
    )
    bot.observe = types.MethodType(Bot._observe_map, bot)
    bot._is_mangja_realm = types.MethodType(Bot._is_mangja_realm, bot)
    bot._is_daily_mangja_realm = types.MethodType(Bot._is_daily_mangja_realm, bot)
    bot._save_daily_secret_started = lambda *_: None
    bot._daily_secret_exit_tick = lambda *_: False
    bot._secret_realm_preset_tick = lambda *_: False
    return bot


class SecretRealmNameTest(unittest.TestCase):
    def test_brackets_and_spaces_are_optional(self):
        self.assertTrue(is_secret_realm_name("[비경] 영험한 청산 지하동굴"))
        self.assertTrue(is_secret_realm_name("비 경] 영험한청산지하동굴"))
        self.assertTrue(is_secret_realm_name("[B.트!JP험한 청산 지하동"))
        self.assertTrue(is_secret_realm_name("[비경] 망자의 마을 3단계"))
        self.assertTrue(is_secret_realm_name("망자의마을"))
        self.assertFalse(is_secret_realm_name("산신의 영역 지하 동굴"))
        self.assertFalse(is_secret_realm_name(""))

    def test_nakhwa_battlefield_is_hunt_only_content(self):
        self.assertTrue(is_hunt_only_content_name("낙화의 전장"))
        self.assertTrue(is_hunt_only_content_name("낙화의전장"))
        self.assertTrue(is_hunt_only_content_name("낙화의』전장,囹-"))
        self.assertTrue(is_hunt_only_content_name("낙화의쩐장"))
        self.assertFalse(is_hunt_only_content_name("안개 기슭"))


class SecretRealmStateTest(unittest.TestCase):
    def test_normal_map_ocr_allows_prefix_loss_and_dialogue_suffix(self):
        self.assertTrue(_same_map_read("바로 성", "타바로 성 끝내주게 우러났어"))
        self.assertFalse(_same_map_read("들 성", "타바로 성"))

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
        self.assertIsNone(bot.farm.current)

    def test_exit_accepts_two_noisy_reads_of_the_same_normal_map(self):
        bot = make_bot()
        bot.observe("[비경] 망자의 마을 3단계", 1.0)
        self.assertTrue(bot.observe("바로 성", 2.0))
        self.assertFalse(bot.observe("타바로 성 깍두기는 덤이야", 3.0))
        self.assertFalse(bot._secret_realm)

    def test_daily_exit_prioritizes_hunt_on_normal_map(self):
        bot = make_bot()
        bot.daily_mode = True
        bot.observe("낙화의 전장", 1.0)
        self.assertTrue(bot.observe("안개 기슭", 2.0))
        self.assertFalse(bot.observe("안개 기슭", 3.0))
        self.assertTrue(bot._daily_return_hunt_pending)
        self.assertTrue(bot._want_hunt)

    def test_nakhwa_noisy_name_does_not_release_content_lock(self):
        bot = make_bot()
        bot.observe("낙화의 전장", 1.0)
        self.assertTrue(bot.observe("사이 전장", 2.0))
        self.assertTrue(bot.observe("낙화 결장", 3.0))
        self.assertTrue(bot._secret_realm)
        self.assertEqual(bot._normal_map_confirm, 0)

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

    def test_daily_nakhwa_restart_recovers_hunt_position_once(self):
        bot = make_bot()
        calls = []
        bot.daily_mode = True
        bot._map_name = "낙화의 전장"
        bot._secret_realm = True
        bot.nakhwa_boss = types.SimpleNamespace(retired_during_event=lambda: False)
        bot.mission = types.SimpleNamespace(
            recover_nakhwa_center=lambda reason: calls.append(reason) or True)
        snap = types.SimpleNamespace(auto_on=True, walking=False)

        Bot._on_secret_realm(bot, snap, 6.0)

        self.assertEqual(calls, ["일일 시작·재실행 위치 확인"])
        self.assertTrue(bot._nakhwa_daily_positioned)
        self.assertEqual(bot._nakhwa_check_at, 26.0)

    def test_secret_realm_retries_hunt_instead_of_pausing(self):
        bot = make_bot()
        bot._hunt_tries = 3
        bot._hunt_press_at = 1.0
        seen = []
        bot._ensure_hunt = lambda _snap, _now: seen.append(bot._hunt_tries)
        snap = types.SimpleNamespace(auto_on=False, walking=False)
        Bot._on_secret_realm(bot, snap, 5.0)
        self.assertEqual(seen, [0])

    def test_mangja_switches_single_below_65_and_aoe_after_recovery(self):
        bot = make_bot()
        switched = []
        bot._map_name = "[비경] 망자의 마을 3단계"
        bot.secret_realm_combat_cfg = {
            "aoe_preset": 4, "single_preset": 2,
            "single_below_hp_percent": 65, "aoe_resume_hp_percent": 90,
            "switch_cooldown_seconds": 5,
        }
        bot.secret_realm_preset = types.SimpleNamespace(
            switch=lambda number: switched.append(number) or True)
        snap = types.SimpleNamespace(frame=object(), state="COMBAT")
        with mock.patch("bot2.brain.hp_ratio", side_effect=[0.64, 0.91]):
            self.assertTrue(Bot._secret_realm_preset_tick(bot, snap, 10.0))
            self.assertTrue(Bot._secret_realm_preset_tick(bot, snap, 20.0))
        self.assertEqual(switched, [2, 4])

    def test_daily_mangja_leaves_after_configured_hour(self):
        bot = make_bot()
        calls = []
        bot.daily_mode = True
        bot._map_name = "[비경] 망자의 마을 3단계"
        bot._daily_secret_started_wall = 1000.0
        bot.mission = types.SimpleNamespace(
            leave_daily_secret_realm=lambda: calls.append("leave") or True)
        with mock.patch("bot2.brain.time.time", return_value=4601.0):
            self.assertTrue(Bot._daily_secret_exit_tick(bot, 100.0))
        self.assertEqual(calls, ["leave"])


class CommonEventPriorityTest(unittest.TestCase):
    @staticmethod
    def _bot(event="보스토벌", map_name="[상시 비경] 원님 고개"):
        calls = []
        bot = types.SimpleNamespace(
            _secret_realm=True, _map_name=map_name,
            _common_event_exit_pending=False, _common_event_exit_at=-1e9,
            _last_progress=0.0, _want_hunt=True, _hunt_tries=2,
            mission=types.SimpleNamespace(
                leave_instance_for_common_event=lambda name: calls.append(name)),
        )
        bot._due_common_event = lambda _wall=None: event
        return bot, calls

    def test_common_event_exits_instance_first(self):
        bot, calls = self._bot()
        snap = types.SimpleNamespace(in_field=True)
        self.assertTrue(Bot._common_event_instance_exit_tick(
            bot, snap, 10.0, datetime(2026, 10, 24, 20, 55)))
        self.assertEqual(calls, ["보스토벌"])
        self.assertTrue(bot._common_event_exit_pending)
        self.assertFalse(bot._want_hunt)

    def test_pending_exit_blocks_duplicate_click(self):
        bot, calls = self._bot()
        bot._common_event_exit_pending = True
        bot._common_event_exit_at = 8.0
        snap = types.SimpleNamespace(in_field=True)
        self.assertTrue(Bot._common_event_instance_exit_tick(bot, snap, 10.0))
        self.assertEqual(calls, [])

    def test_target_nakhwa_instance_is_not_exited(self):
        bot, calls = self._bot("낙화의 전장", "낙화의 전장")
        snap = types.SimpleNamespace(in_field=True)
        self.assertFalse(Bot._common_event_instance_exit_tick(bot, snap, 10.0))
        self.assertEqual(calls, [])

    def test_normal_map_releases_event_task(self):
        bot, calls = self._bot()
        bot._secret_realm = False
        bot._common_event_exit_pending = True
        snap = types.SimpleNamespace(in_field=True)
        self.assertFalse(Bot._common_event_instance_exit_tick(bot, snap, 10.0))
        self.assertFalse(bot._common_event_exit_pending)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
