import threading
import types
import unittest
from unittest.mock import patch

from bot import actions
from bot.vision import Match
from bot2.brain import Bot
from bot2.farm import FarmTask, StopRequested, guide_spots
from bot2.risk import RiskGuard
from bot2.livestatus import read_target
from tests.test_evade import make_bot, snap


class RiskTest(unittest.TestCase):
    @patch('bot2.livestatus.target_fill', return_value=0.8)
    @patch('bot2.livestatus._ocr', side_effect=['1170K 우물 역병쥐', '170K 우물 역병쥐'])
    @patch('bot2.livestatus._crop', return_value='crop')
    def test_target_power_removes_sword_icon_false_leading_one(
            self, _crop_mock, _ocr_mock, _fill_mock):
        target = read_target('frame')
        self.assertEqual(target['power'], 170000)
        self.assertEqual(target['name'], '우물 역병쥐')

    def test_two_distinct_matching_frames_confirm_danger(self):
        guard = RiskGuard(1.5)
        guard.arm(0)
        target = {'name': '우물 왕곰쥐', 'power': 215000}
        self.assertFalse(guard.observe(104000, target, 1))
        self.assertFalse(guard.observe(104000, target, 1))
        self.assertTrue(guard.observe(104000, target, 3))
        self.assertFalse(guard.due(4))

    def test_boundary_unknown_and_changed_target_do_not_confirm(self):
        guard = RiskGuard(1.5)
        guard.arm(0)
        self.assertFalse(guard.observe(100000, {'name': 'safe', 'power': 150000}, 1))
        self.assertFalse(guard.due(2))
        guard.arm(3)
        self.assertFalse(guard.observe(None, {'name': 'hard', 'power': 200000}, 4))
        self.assertFalse(guard.observe(100000, {'name': 'a', 'power': 200000}, 5))
        self.assertFalse(guard.observe(100000, {'name': 'b', 'power': 200000}, 7))
        self.assertFalse(guard.due(24))

    def test_emergency_guide_pick_is_within_power_and_respects_death_bans(self):
        farm = FarmTask.__new__(FarmTask)
        farm.margin, farm.reselect, farm.banned = 1.5, 900, {}
        picked = []
        farm._travel = lambda spot, now: picked.append(spot) or True
        self.assertTrue(farm.escape(104000, 0))
        self.assertLessEqual(picked[-1]['power'], 104000)
        banned_power = picked[-1]['power']
        farm.banned = {spot['id']: 500 for spot in guide_spots() if spot['power'] >= banned_power}
        self.assertTrue(farm.escape(104000, 1))
        self.assertLess(picked[-1]['power'], banned_power)

    def test_low_hp_uses_road_evade_and_returns_after_full_recovery(self):
        bot = make_bot(0.2)
        bot.power = 104000
        bot.farm = types.SimpleNamespace(power=104000, escape=lambda power, now: bot.calls.append('teleport') or True)
        bot.tick(snap(True), 0)
        bot.tick(snap(False), 5)
        self.assertEqual(bot.calls, ['flee'])
        bot.hp = 1.0
        bot.tick(snap(False, 'IDLE'), 10)
        self.assertEqual(bot.calls, ['flee', ('back', (380, 0))])
        self.assertEqual(bot._rest, 'on')

    def test_startup_risk_moves_or_pauses_on_failure(self):
        for successful in (True, False):
            alerts = []
            bot = types.SimpleNamespace(risk_guard=RiskGuard(), power=104000,
                                        _boss_near=lambda *_: False,
                                        _escape_via_guide=lambda *_: successful,
                                        _alert=lambda text, frame: alerts.append(text))
            bot.risk_guard.arm(0)
            frame = types.SimpleNamespace(state='COMBAT', walking=False, frame='frame')
            with patch('bot2.livestatus.read_target', return_value={'name': 'hard', 'power': 215000}):
                self.assertFalse(Bot._risk_tick(bot, frame, 1))
                self.assertTrue(Bot._risk_tick(bot, frame, 3))
            if successful:
                self.assertEqual(bot._evade, {'teleported': True})
                self.assertEqual(bot._rest, 'rest')
            else:
                self.assertEqual(len(alerts), 1)


class InputGuardTest(unittest.TestCase):
    def make_bot(self):
        return types.SimpleNamespace(stop_event=threading.Event(), paused=False, user_detect=False,
            window=types.SimpleNamespace(locate=lambda: True, is_minimized=lambda: False, is_foreground=lambda: True))

    def test_stop_pause_and_focus_loss_block_even_with_detection_off(self):
        bot = self.make_bot()
        Bot._check_user(bot)
        bot.stop_event.set()
        with self.assertRaises(StopRequested):
            Bot._check_user(bot)
        bot.stop_event.clear()
        bot.paused = True
        with self.assertRaises(actions.UserInterrupt):
            Bot._check_user(bot)
        bot.paused = False
        bot.window.is_foreground = lambda: False
        with self.assertRaises(actions.UserInterrupt):
            Bot._check_user(bot)

    def test_cua_background_capture_allows_focus_loss(self):
        bot = self.make_bot()
        bot.window.is_foreground = lambda: False
        bot.window.background_capture = True
        Bot._check_user(bot)

    def test_cua_input_backend_allows_focus_before_real_input(self):
        bot = self.make_bot()
        bot.window.is_foreground = lambda: False
        bot.window.background_capture = False
        bot.window.cua_input_enabled = True
        Bot._check_user(bot)

    def test_physical_input_is_refused_without_cua(self):
        old = actions._input_backend
        actions._input_backend = None
        try:
            with self.assertRaisesRegex(RuntimeError, "물리 입력 없이"):
                actions.drag(1, 1, 10, 10)
        finally:
            actions._input_backend = old

    def test_clan_help_rechecks_field_and_icon_before_click(self):
        found = {
            "clan_help.png": Match("clan_help.png", 52, 822, 0.96),
            "field_hud.png": Match("field_hud.png", 1630, 1017, 0.97),
        }
        matcher = types.SimpleNamespace(find=lambda _frame, name, *_args: found.get(name))
        clicks = []
        bot = types.SimpleNamespace(
            cfg={"clan_help": {"enabled": True, "template": "clan_help.png", "cooldown": 30},
                 "field_indicator": {"template": "field_hud.png"}},
            matcher=matcher, dry_run=False, _clan_help_at=-1e9,
            window=types.SimpleNamespace(grab=lambda: "fresh"),
            _check_user=lambda: None,
            _click_frame=lambda label, x, y: clicks.append((label, x, y)),
        )
        snap = types.SimpleNamespace(in_field=True, frame="old")
        self.assertTrue(Bot._clan_help_tick(bot, snap, 100))
        self.assertEqual(clicks, [("문파 도움", 52, 822)])
        self.assertFalse(Bot._clan_help_tick(bot, snap, 101))
        snap.state = "DIALOG"
        bot._clan_help_at = -1e9
        self.assertFalse(Bot._clan_help_tick(bot, snap, 200))

    def test_clan_help_cancels_if_icon_disappears_before_input(self):
        calls = {"clan": 0}

        def find(_frame, name, *_args):
            if name == "field_hud.png":
                return Match(name, 1630, 1017, 0.97)
            calls["clan"] += 1
            return Match(name, 52, 822, 0.96) if calls["clan"] == 1 else None

        clicks = []
        bot = types.SimpleNamespace(
            cfg={"clan_help": {"enabled": True, "template": "clan_help.png"},
                 "field_indicator": {"template": "field_hud.png"}},
            matcher=types.SimpleNamespace(find=find), dry_run=False, _clan_help_at=-1e9,
            window=types.SimpleNamespace(grab=lambda: "fresh"), _check_user=lambda: None,
            _click_frame=lambda *args: clicks.append(args),
        )
        self.assertFalse(Bot._clan_help_tick(bot, types.SimpleNamespace(in_field=True, frame="old"), 100))
        self.assertEqual(clicks, [])


if __name__ == '__main__':
    unittest.main()
