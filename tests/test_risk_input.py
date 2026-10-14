import threading
import types
import unittest
from unittest.mock import patch

from bot import actions
from bot2.brain import Bot
from bot2.farm import FarmTask, StopRequested, guide_spots
from bot2.risk import RiskGuard
from tests.test_evade import make_bot, snap


class RiskTest(unittest.TestCase):
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

    def test_teleport_evade_recovers_without_returning_to_danger(self):
        bot = make_bot(0.2)
        bot.power = 104000
        bot.farm = types.SimpleNamespace(power=104000, escape=lambda power, now: bot.calls.append('teleport') or True)
        bot.tick(snap(True), 0)
        bot.tick(snap(False), 5)
        self.assertEqual(bot.calls, ['teleport'])
        bot.hp = 1.0
        bot.tick(snap(False, 'IDLE'), 10)
        self.assertEqual(bot.calls, ['teleport'])
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

    def test_ctrl_is_released_when_press_fails(self):
        with patch.object(actions.pydirectinput, 'keyDown') as down, patch.object(actions.pydirectinput, 'keyUp') as up, \
             patch.object(actions.pydirectinput, 'press', side_effect=RuntimeError('input failed')):
            with self.assertRaises(RuntimeError):
                actions.hotkey('ctrl+1')
        down.assert_called_once_with('ctrl')
        up.assert_called_once_with('ctrl')

    def test_mouse_is_released_when_drag_fails(self):
        with patch.object(actions.pydirectinput, 'moveTo', side_effect=[None, RuntimeError('failed')]), \
             patch.object(actions.pydirectinput, 'mouseDown'), patch.object(actions.pydirectinput, 'mouseUp') as up:
            with self.assertRaises(RuntimeError):
                actions.drag(1, 1, 10, 10)
        up.assert_called_once()


if __name__ == '__main__':
    unittest.main()
