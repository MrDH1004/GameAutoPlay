"""일반 모드: 현재 위치 사냥과 추천 사냥터 피신·원래 지역 복귀."""
import types
import unittest

from bot2.brain import Bot


def make_bot():
    b = types.SimpleNamespace(
        cfg={"farm": {}, "collect": {"enabled": False}}, normal_mode=False,
        _cfg_normal_mode=False, _normal_mode_request=None, manual_farm=False,
        farm_mode=False, _manual_farm_request=None, _farm_until=10.0,
        _retry_quest="x", _need_switch=True, _collect_pending=True,
        collect=None, farm=types.SimpleNamespace(), _normal_origin={"old": True},
        _normal_returning=True, _evade_prep_at=10.0, _evade_prep_key="old",
        _want_hunt=False, _hunt_tries=2, _hunt_press_at=3.0,
        _fails=2, _click_at=2.0, _idle_since=1.0, _verify="x",
        waiting_user=True, _mission_priority_pending=True, mission=None,
        notifier=types.SimpleNamespace(send=lambda *a, **k: None),
        activity=types.SimpleNamespace(ignore_until_now=lambda *a: None),
    )
    b.focused = []
    b._focus_game_window = lambda: b.focused.append(True)
    b.request = types.MethodType(Bot.request_normal_mode, b)
    b.apply = types.MethodType(Bot._apply_normal_mode, b)
    return b


class NormalModeSwitchTest(unittest.TestCase):
    def test_on_keeps_current_position_and_disables_other_farm_mode(self):
        b = make_bot()
        b.farm_mode = True
        b.manual_farm = True
        b.request(True, force=True)
        b.apply(100.0)
        self.assertTrue(b.normal_mode)
        self.assertFalse(b.farm_mode)
        self.assertFalse(b.manual_farm)
        self.assertFalse(b._mission_priority_pending)
        self.assertEqual(b._farm_until, 0.0)
        self.assertIsNone(b._normal_origin)
        self.assertTrue(b._want_hunt)
        self.assertEqual(b.focused, [True])

    def test_off_returns_to_quest_mode(self):
        b = make_bot()
        b.normal_mode = True
        b.request(False, force=True)
        b.apply(100.0)
        self.assertFalse(b.normal_mode)
        self.assertIsNone(b._normal_origin)


class NormalModeEscapeTest(unittest.TestCase):
    def test_current_area_is_connected_to_return_spot_db(self):
        b = types.SimpleNamespace(
            evader=types.SimpleNamespace(cache={"here": {"name": "호랑이골"}}),
            _normal_origin=None,
        )
        self.assertTrue(Bot._remember_normal_origin(b))
        self.assertEqual(b._normal_origin["spot"]["name"], "호랑이골")

    def test_guide_escape_requires_saved_return_origin(self):
        b = types.SimpleNamespace(
            farm=types.SimpleNamespace(power=100000, escape=lambda *_: True),
            hp_cfg={"evade_guide_first": True}, power=100000,
            normal_mode=True, _normal_origin=None,
        )
        self.assertFalse(Bot._escape_via_guide(b, 1.0))
        b._normal_origin = {"name": "원래 지역"}
        self.assertTrue(Bot._escape_via_guide(b, 2.0))

    def test_low_hp_uses_map_road_instead_of_guide(self):
        from tests.test_evade import make_bot, snap
        b = make_bot(0.18)
        b.normal_mode = True
        b._normal_origin = {"name": "원래 지역"}
        guide_calls = []
        b.farm = types.SimpleNamespace(
            power=100000, escape=lambda *_: guide_calls.append(True) or True)
        b.tick(snap(True), 0.0)
        b.tick(snap(False), 5.0)
        self.assertEqual(b._evade, (380, 0))
        self.assertEqual(guide_calls, [])
        b.hp = 1.0
        b.tick(snap(False, "IDLE"), 10.0)
        self.assertIn(("back", (380, 0)), b.calls)


class NormalModeCommonTaskTest(unittest.TestCase):
    def _bot(self, yeonggi_result=False, common_result=False):
        calls = []
        b = types.SimpleNamespace(
            farm=None, _last_map_close=0.0, _power_at=100.0, normal_mode=True,
            _last_progress=0.0, _retry_quest="quest", _need_switch=True,
            _normal_returning=False, _want_hunt=False, _hunt_check=100.0,
        )
        b._maybe_prepare_evade = lambda *_: None
        b._rest_tick = lambda *_: False
        b._watch_move_end = lambda *_: None
        b._run_yeonggi_if_due = lambda *_: calls.append("yeonggi") or yeonggi_result
        b._run_common_field_tasks = lambda *_: calls.append("common") or common_result
        b.calls = calls
        b.on_field = types.MethodType(Bot._on_field, b)
        return b

    def test_normal_mode_checks_yeonggi_before_hunting(self):
        b = self._bot(yeonggi_result=True)
        b.on_field(types.SimpleNamespace(frame=None, state="IDLE", walking=False, auto_on=True), 10.0)
        self.assertEqual(b.calls, ["yeonggi"])

    def test_normal_mode_runs_other_common_tasks(self):
        b = self._bot(common_result=True)
        b.on_field(types.SimpleNamespace(frame=None, state="IDLE", walking=False, auto_on=True), 10.0)
        self.assertEqual(b.calls, ["yeonggi", "common"])

    def test_normal_mode_turns_on_hunt_before_common_tasks(self):
        b = self._bot(common_result=True)
        b._ensure_hunt = lambda *_: b.calls.append("hunt")
        b.on_field(types.SimpleNamespace(frame=None, state="IDLE", walking=False, auto_on=False), 10.0)
        self.assertEqual(b.calls, ["hunt"])


class NormalModeMissionGuardTest(unittest.TestCase):
    def test_normal_mode_never_opens_mission_window(self):
        called = []
        b = types.SimpleNamespace(
            normal_mode=True,
            mission=types.SimpleNamespace(run=lambda *_args, **_kwargs: called.append(True)),
            stats={"missions": 0})
        self.assertFalse(Bot._run_mission(b, 1.0))
        self.assertEqual(called, [])


class DailyModeTest(unittest.TestCase):
    def _bot(self, next_ok):
        calls = []
        b = types.SimpleNamespace(
            farm=None, _last_map_close=0.0, _power_at=100.0,
            daily_mode=True, normal_mode=False, _secret_realm=False,
            mission=types.SimpleNamespace(next_ok=next_ok, _daily_intake_checked=True,
                                          tab_blocked_until={"일일": 999.0}),
            _last_progress=0.0, _retry_quest="본편", _need_switch=True,
            _want_hunt=False, _hunt_check=100.0,
            _daily_return_hunt_pending=False,
        )
        b._maybe_prepare_evade = lambda *_: None
        b._rest_tick = lambda *_: False
        b._watch_move_end = lambda *_: None
        b._run_accepted_daily = lambda *_: calls.append("tracker") or False
        b._run_priority_mission = lambda *_, **__: calls.append("daily") or False
        b._finish_daily_to_farm = lambda reason: calls.append("farm")
        b._run_yeonggi_if_due = lambda *_: calls.append("yeonggi") or False
        b._run_common_field_tasks = lambda *_: calls.append("common") or False
        b._ensure_hunt = lambda *_: calls.append("hunt")
        b.calls = calls
        b.on_field = types.MethodType(Bot._on_field, b)
        return b

    def test_daily_mode_checks_only_daily_mission(self):
        b = self._bot(next_ok=0.0)
        b.on_field(types.SimpleNamespace(frame=None, state="IDLE", walking=False, auto_on=True), 10.0)
        self.assertEqual(b.calls, ["yeonggi", "tracker", "daily", "farm"])
        self.assertIsNone(b._retry_quest)
        self.assertFalse(b._need_switch)

    def test_daily_tracker_selection_precedes_mission_window(self):
        b = self._bot(next_ok=0.0)
        b._run_accepted_daily = lambda *_: b.calls.append("tracker") or True

        b.on_field(types.SimpleNamespace(frame=None, state="IDLE", walking=False, auto_on=True), 10.0)

        self.assertEqual(b.calls, ["yeonggi", "tracker"])

    def test_tracker_absence_cross_checks_daily_mission_tab_before_finishing(self):
        b = self._bot(next_ok=0.0)
        b._daily_absence_confirmed = True
        b._daily_missing_count = 2
        b._run_priority_mission = lambda *_, **__: b.calls.append("daily") or True

        b.on_field(types.SimpleNamespace(frame=None, state="IDLE", walking=False, auto_on=True), 10.0)

        self.assertEqual(b.calls, ["yeonggi", "tracker", "daily"])
        self.assertNotIn("farm", b.calls)
        self.assertFalse(b._daily_absence_confirmed)
        self.assertFalse(b.mission._daily_intake_checked)
        self.assertNotIn("일일", b.mission.tab_blocked_until)

    def test_daily_mode_turns_on_hunt_before_tracker_and_common_tasks(self):
        b = self._bot(next_ok=0.0)
        b.on_field(types.SimpleNamespace(frame=None, state="IDLE", walking=False, auto_on=False), 10.0)
        self.assertEqual(b.calls, ["hunt"])

    def test_daily_completion_queues_farm_mode_and_persistence(self):
        b = types.SimpleNamespace(
            daily_mode=True, _daily_mode_request=None, _normal_mode_request=None,
            _manual_farm_request=None, _mode_persist_request=None)

        Bot._finish_daily_to_farm(b, "완료")

        self.assertFalse(b._daily_mode_request)
        self.assertFalse(b._normal_mode_request)
        self.assertTrue(b._manual_farm_request)
        self.assertEqual(b._mode_persist_request,
                         {"farm": True, "normal": False, "daily": False})

    def test_daily_absence_requires_two_full_tracker_scans(self):
        tracker = types.SimpleNamespace(click_daily=lambda _expected: "")
        mission = types.SimpleNamespace(next_ok=0.0, cfg={"cooldown": 90}, last_daily_title="")
        b = types.SimpleNamespace(
            tracker=tracker, mission=mission, cfg={"daily_mode": {"absence_confirm_seconds": 8}},
            _daily_missing_count=0, _daily_missing_at=0.0, _daily_absence_confirmed=False)

        self.assertFalse(Bot._run_accepted_daily(b, 10.0))
        self.assertEqual(b._daily_missing_count, 1)
        self.assertFalse(b._daily_absence_confirmed)
        self.assertFalse(Bot._run_accepted_daily(b, 18.0))
        self.assertTrue(b._daily_absence_confirmed)

    def test_hidden_active_daily_gets_three_minutes_to_finish(self):
        tracker = types.SimpleNamespace(click_daily=lambda _expected: "")
        mission = types.SimpleNamespace(
            next_ok=0.0, cfg={"cooldown": 90}, last_daily_title="[일일] 황무지 마을 요귀 처치")
        b = types.SimpleNamespace(
            tracker=tracker, mission=mission,
            cfg={"daily_mode": {"absence_confirm_seconds": 8,
                                "active_absence_confirm_seconds": 180}},
            _daily_missing_count=0, _daily_missing_at=0.0, _daily_absence_confirmed=False)

        self.assertFalse(Bot._run_accepted_daily(b, 10.0))
        self.assertEqual(mission.next_ok, 190.0)
        self.assertFalse(Bot._run_accepted_daily(b, 30.0))
        self.assertFalse(b._daily_absence_confirmed)
        self.assertFalse(Bot._run_accepted_daily(b, 190.0))
        self.assertTrue(b._daily_absence_confirmed)

    def test_daily_mode_keeps_common_tasks_after_daily_check(self):
        b = self._bot(next_ok=999.0)
        b.on_field(types.SimpleNamespace(frame=None, state="IDLE", walking=False, auto_on=True), 10.0)
        self.assertEqual(b.calls, ["yeonggi", "common"])

    def test_daily_mode_restores_hunt_before_reopening_missions(self):
        b = self._bot(next_ok=0.0)
        b._daily_return_hunt_pending = True
        b.on_field(types.SimpleNamespace(frame=None, state="IDLE", walking=False, auto_on=False), 10.0)
        self.assertEqual(b.calls, ["hunt"])

        b.calls.clear()
        b.on_field(types.SimpleNamespace(frame=None, state="COMBAT", walking=False, auto_on=True), 11.0)
        self.assertEqual(b.calls, [])
        self.assertFalse(b._daily_return_hunt_pending)
        self.assertEqual(b.mission.next_ok, 41.0)

    def test_five_of_five_runs_accepted_daily_from_tracker(self):
        tracker = types.SimpleNamespace(
            scanned_at=10.0, entries=[], click_text=lambda marker: "[일일] 비경 탐험 참여")
        b = types.SimpleNamespace(
            tracker=tracker,
            mission=types.SimpleNamespace(
                next_ok=0.0, cfg={"cooldown": 90},
                enter_accepted_daily_secret_realm=lambda: True),
            quest_cfg={"verify_seconds": 7.0}, _source="", _click_at=0.0,
            _idle_since=1.0, _verify=None, _clicked_xy=(1, 2))

        self.assertTrue(Bot._run_accepted_daily(b, 20.0))
        self.assertEqual(b._source, "일일")
        self.assertEqual(b.mission.next_ok, 25.0)

    def test_accepted_secret_realm_uses_dedicated_stage_three_flow(self):
        entered = []
        tracker = types.SimpleNamespace(
            click_text=lambda _marker: "[일일] 비경 탐험 참여")
        mission = types.SimpleNamespace(
            next_ok=0.0, cfg={"cooldown": 90},
            enter_accepted_daily_secret_realm=lambda: entered.append(True) or True)
        b = types.SimpleNamespace(
            tracker=tracker, mission=mission, quest_cfg={"verify_seconds": 7.0},
            _source="", _click_at=0.0, _idle_since=1.0, _verify=None,
            _clicked_xy=(1, 2))

        self.assertTrue(Bot._run_accepted_daily(b, 20.0))
        self.assertEqual(entered, [True])

    def test_nakhwa_daily_uses_dedicated_entry_flow(self):
        entered = []
        tracker = types.SimpleNamespace(
            click_text=lambda _marker: "[일일] 낙화의 전장 요귀 처치")
        mission = types.SimpleNamespace(
            next_ok=0.0, cfg={"cooldown": 90},
            _enter_nakhwa_battlefield=lambda: entered.append(True) or True)
        b = types.SimpleNamespace(
            tracker=tracker, mission=mission, quest_cfg={"verify_seconds": 7.0},
            _source="", _click_at=0.0, _idle_since=1.0, _verify=None,
            _clicked_xy=(1, 2))

        self.assertTrue(Bot._run_accepted_daily(b, 20.0))
        self.assertEqual(entered, [True])


class StoryModeTest(unittest.TestCase):
    def test_story_mode_only_runs_story_priority(self):
        calls = []
        b = types.SimpleNamespace(
            tracker=None, farm=None, _last_map_close=0.0, _power_at=100.0,
            story_mode=True, daily_mode=False, normal_mode=False, _secret_realm=False,
            mission=types.SimpleNamespace(next_ok=0.0), _last_progress=0.0,
            _retry_quest="본편", _need_switch=True)
        b._maybe_prepare_evade = lambda *_: None
        b._rest_tick = lambda *_: False
        b._watch_move_end = lambda *_: None
        b._run_priority_mission = lambda *_, **__: calls.append("story") or True
        b._run_yeonggi_if_due = lambda *_: calls.append("yeonggi") or False
        b._run_common_field_tasks = lambda *_: calls.append("common") or False

        Bot._on_field(b, types.SimpleNamespace(
            frame=None, state="IDLE", walking=False, auto_on=False), 10.0)

        self.assertEqual(calls, ["yeonggi", "story"])
        self.assertIsNone(b._retry_quest)
        self.assertFalse(b._need_switch)

if __name__ == "__main__":
    unittest.main()
