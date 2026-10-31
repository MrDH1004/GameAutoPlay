"""UI 리뉴얼의 설정 저장·검색·실행 제어 회귀 검사. 게임 입력은 보내지 않는다."""
import json
import logging
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('DOKKAEBI_TEST', '1')

from PySide6.QtWidgets import QApplication, QSizePolicy
import gui


class RenewalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'settings.json'
        self.path.write_text(json.dumps({'farm/margin': 1.3, 'collect/enabled': False,
                                         '_geometry': [10, 10, 1000, 720, False]}), encoding='utf-8')
        self.settings_patch = patch.object(gui, 'SETTINGS_PATH', str(self.path))
        self.settings_patch.start()
        self.win = gui.Window()
        self.win.timer.stop()
        self.win._geo_timer.stop()

    def tearDown(self):
        self.win.thread = None
        self.win.close()
        if self.win.tray:
            self.win.tray.hide()
        self.win.deleteLater()
        self.app.processEvents()
        self.settings_patch.stop()
        self.temp.cleanup()

    def test_geometry_save_does_not_save_pending_setting_changes(self):
        self.win.controls[('farm', 'margin')].setValue(1.5)
        self.assertTrue(self.win.btn_save_settings.isEnabled())
        self.win._save_geometry_now()
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['farm/margin'], 1.3)
        self.win._save_settings()
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['farm/margin'], 1.5)
        self.assertFalse(self.win.btn_save_settings.isEnabled())

    def test_profile_cannot_enable_user_excluded_automation(self):
        self.win._apply_values({'collect/enabled': True, 'rewards/enabled': True})
        self.win._set_running(False)
        for path in gui.LOCKED_SETTINGS:
            self.assertFalse(self.win.controls[path].isChecked())
            self.assertFalse(self.win.controls[path].isEnabled())
        cfg = self.win._build_config()
        self.assertFalse(cfg['collect']['enabled'])
        self.assertFalse(cfg['rewards']['enabled'])
        self.assertFalse(cfg['hp']['potion_normal'])

    def test_search_hides_unrelated_settings_and_reset_restores_them(self):
        self.win._filter_settings('물약')
        matches = [text for _, text, widgets in self.win.settings_rows if not widgets[0].isHidden()]
        self.assertTrue(matches)
        self.assertTrue(all('물약' in text for text in matches))
        self.win._filter_settings('no-such-setting')
        self.assertFalse(self.win.settings_empty.isHidden())
        self.win._filter_settings('')
        self.assertTrue(all(not widgets[0].isHidden() for _, _, widgets in self.win.settings_rows))

    def test_navigation_and_keyboard_search_stay_in_sync(self):
        self.win.nav_group.button(3).click()
        self.assertEqual(self.win.tabs.currentIndex(), 3)
        self.win._focus_settings_search()
        self.assertEqual(self.win.tabs.currentIndex(), 1)
        self.assertTrue(self.win.nav_group.button(1).isChecked())

    def test_updates_are_in_sidebar_with_automatic_release_check(self):
        names = [self.win.tabs.tabText(i) for i in range(self.win.tabs.count())]
        self.assertIn("업데이트", names)
        page = self.win.tabs.widget(names.index("업데이트"))
        self.assertIn("업데이트 내역", page.toPlainText())
        self.assertIn("GitHub", page.toPlainText())
        self.assertTrue(hasattr(self.win, "_show_update_prompt"))

    def test_mode_flow_is_available_in_workspace(self):
        names = [self.win.tabs.tabText(i) for i in range(self.win.tabs.count())]
        self.assertIn("동작 흐름", names)
        self.assertIn("전체 상태 흐름", self.win.flow_view.toPlainText())
        self.win.flow_combo.setCurrentIndex(self.win.flow_combo.findData("daily"))
        self.assertIn("망자의 마을 3단계", self.win.flow_view.toPlainText())
        self.assertIn("일반 맵 AUTO", self.win.flow_view.toPlainText())

    def test_mode_flow_follows_live_bot_state(self):
        bot = types.SimpleNamespace(
            daily_mode=True, normal_mode=False, farm_mode=False, manual_farm=False,
            paused=False, waiting_user=False, _last_state="COMBAT", _rest=None,
            _evade=None, _evade_pending=False, _secret_realm=True,
            _map_name="[비경] 망자의 마을", _daily_return_hunt_pending=False,
            _want_hunt=False,
            live=types.SimpleNamespace(data={"map": "[비경] 망자의 마을", "hp": .82, "auto_on": True}))
        self.win.flow_follow.setChecked(True)
        self.win._refresh_flow_runtime(bot)
        self.assertEqual(self.win.flow_combo.currentData(), "daily")
        self.assertIn("AUTO 켜짐", self.win.flow_live["auto"].text())
        self.assertIn("현재", self.win.flow_view.toPlainText())
        self.assertIn("전투 콘텐츠 내부", self.win.flow_decision.text())

    def test_theme_option_switches_and_persists(self):
        self.assertEqual(self.win.theme_combo.currentData(), "dark")
        self.win.theme_combo.setCurrentIndex(self.win.theme_combo.findData("light"))
        self.assertEqual(self.win._theme_name, "light")
        self.assertEqual(gui.C["bg"], "#f3f6fb")
        self.assertTrue(self.win._save())
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["_theme"], "light")

    def test_daily_only_mode_is_available(self):
        self.assertEqual(self.win.btn_daily.text(), "일일 전용")
        self.assertIn(("daily_mode", "enabled"), self.win.controls)
        self.assertIn(("secret_realm_combat", "aoe_preset"), self.win.controls)
        self.assertIn(("secret_realm_combat", "single_preset"), self.win.controls)
        self.assertIn(("secret_realm_combat", "single_below_hp_percent"), self.win.controls)
        self.assertIn(("secret_realm_combat", "daily_duration_minutes"), self.win.controls)
        self.assertTrue(self.win.daily_card.isHidden())

        self.win.start = lambda: None
        self.win.toggle_daily(True)
        self.assertFalse(self.win.daily_card.isHidden())
        self.win.toggle_daily(False)
        self.assertTrue(self.win.daily_card.isHidden())

    def test_story_only_mode_is_available(self):
        self.assertEqual(self.win.btn_story.text(), "서사 전용")
        self.assertIn(("story_mode", "enabled"), self.win.controls)
        self.win.start = lambda: None
        self.win.toggle_story(True)
        self.assertTrue(self.win.btn_story.isChecked())
        self.assertFalse(self.win.btn_daily.isChecked())
        self.assertFalse(self.win.btn_normal.isChecked())

    def test_daily_quest_picker_builds_checkable_list(self):
        self.win._show_daily_quests(["[일일] 낙화의 전장 요귀 처치", "[일일] 비경 탐험 참여"])
        self.assertEqual(len(self.win.daily_quest_checks), 2)
        self.assertFalse(self.win.btn_daily_selected.isEnabled())
        self.win.daily_quest_checks["[일일] 비경 탐험 참여"].setChecked(True)
        self.assertTrue(self.win.btn_daily_selected.isEnabled())

    def test_daily_quest_catalog_is_saved_and_restored(self):
        titles = ["[일일] 명상 수련하기", "[일일] 황무지 마을 요귀 처치"]
        self.win._show_daily_quests(titles, remember=True)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["_daily_quest_catalog"], titles)

        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        self.win = gui.Window()
        self.win.timer.stop()
        self.win._geo_timer.stop()
        self.assertEqual(list(self.win.daily_quest_checks), titles)
        self.assertIn("최근 불러온", self.win.lbl_daily_picker.text())

    def test_empty_daily_scan_keeps_last_successful_catalog(self):
        titles = ["[일일] 명상 수련하기"]
        self.win._show_daily_quests(titles, remember=True)
        bot = types.SimpleNamespace(daily_picker_state={
            "revision": 1, "status": "empty", "items": [], "message": "목록을 읽지 못했습니다"})
        self.win._refresh_daily_picker(bot)
        self.assertEqual(list(self.win.daily_quest_checks), titles)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["_daily_quest_catalog"], titles)

    def test_owned_skill_selection_is_saved_for_recommendations(self):
        calls = []
        self.win._start_skill_learning = lambda force=False: calls.append(force)
        self.win.skill_owned_checks["찌르기"].setChecked(True)
        self.win.skill_owned_checks["부적 던지기"].setChecked(True)
        self.win._save_owned_skills()

        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("찌르기", saved["_owned_skills"])
        self.assertIn("부적 던지기", saved["_owned_skills"])
        self.assertEqual(calls, ["owned"])

    def test_mode_toggles_persist_and_default_to_normal(self):
        self.assertTrue(self.win.btn_normal.isChecked())
        self.assertFalse(self.win.btn_farm.isChecked())
        self.assertFalse(self.win.btn_daily.isChecked())
        self.assertTrue(self.win._persist_mode_toggles(daily=True))
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual((saved["farm/enabled"], saved["normal_mode/enabled"],
                          saved["daily_mode/enabled"]), (False, False, True))
        loaded = self.win._load_saved()
        self.assertTrue(loaded["daily_mode/enabled"])

    def test_dashboard_long_cua_error_is_compact_and_wraps_without_widening_cards(self):
        long_error = "Cua Driver run_steps 실패: " + ("observation payload " * 80)
        text = self.win._recent_dashboard_text([(logging.WARNING, long_error)])
        self.assertLessEqual(len(text), 180)
        self.assertTrue(text.endswith("…"))
        self.assertTrue(self.win.lbl_recent.wordWrap())
        self.assertEqual(self.win.lbl_recent.sizePolicy().horizontalPolicy(), QSizePolicy.Ignored)

    def test_all_toggle_choices_persist_immediately_without_saving_pending_numbers(self):
        self.win.controls[("farm", "margin")].setValue(1.5)
        toggle = self.win.controls[("boss_hunt", "enabled")]
        toggle.setChecked(not toggle.isChecked())
        self.win.chk_debug.setChecked(True)

        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["boss_hunt/enabled"], toggle.isChecked())
        self.assertTrue(saved["_debug"])
        self.assertEqual(saved["farm/margin"], 1.3)
        self.assertTrue(self.win.btn_save_settings.isEnabled())

    def test_stop_is_recorded_while_bot_is_initializing(self):
        self.win.thread = types.SimpleNamespace(bot=None, stop_requested=False)
        self.win.stop()
        self.assertTrue(self.win.thread.stop_requested)
        self.assertFalse(self.win.btn_stop.isEnabled())

    def test_save_failure_keeps_unsaved_changes(self):
        self.win.controls[('farm', 'margin')].setValue(1.5)
        with patch('gui.save_json', side_effect=PermissionError('denied')):
            self.win._save_settings()
        self.assertIn('저장 실패', self.win.settings_status.text())
        self.assertTrue(self.win.btn_save_settings.isEnabled())

    def test_invalid_profile_cannot_partially_apply(self):
        before = self.win.controls[('farm', 'margin')].value()
        with self.assertRaises(ValueError):
            self.win._apply_values({'farm/margin': 1.5, 'yeonggi/interval': 'broken'})
        self.assertEqual(self.win.controls[('farm', 'margin')].value(), before)

    def test_error_and_pending_requests_are_not_lost_on_thread_finish(self):
        self.win._on_failed('capture unavailable')
        self.win.thread = types.SimpleNamespace(wait=lambda: None, deleteLater=lambda: None)
        self.win._pending_potion = self.win._pending_farm = True
        self.win._on_finished()
        self.win._refresh()
        self.assertIn('오류', self.win.lbl_state.text())
        self.assertIn('capture unavailable', self.win.notice.text())
        self.assertFalse(self.win._pending_potion or self.win._pending_farm)

    def test_close_waits_asynchronously_for_running_bot(self):
        from PySide6.QtGui import QCloseEvent
        self.win._quitting = True
        self.win.thread = types.SimpleNamespace(bot=None, stop_requested=False)
        event = QCloseEvent()
        self.win.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.win._closing)
        self.assertTrue(self.win.thread.stop_requested)

    def test_window_close_no_longer_hides_running_helper_in_tray(self):
        from PySide6.QtGui import QCloseEvent
        self.win._quitting = False
        self.win.thread = types.SimpleNamespace(bot=None, stop_requested=False)
        self.win.hide = lambda: self.fail("창 닫기에서 트레이 숨김을 사용하면 안 됩니다")
        event = QCloseEvent()
        self.win.closeEvent(event)
        self.assertTrue(self.win._quitting)
        self.assertTrue(self.win._closing)
        self.assertTrue(self.win.thread.stop_requested)

    def test_app_exit_terminates_skill_learning_child(self):
        calls = []
        process = types.SimpleNamespace(
            pid=4321,
            poll=lambda: None,
            terminate=lambda: calls.append("terminate"),
            wait=lambda timeout: calls.append(("wait", timeout)),
            kill=lambda: calls.append("kill"),
        )
        self.win._skill_learning_process = process
        with patch("gui.subprocess.run", return_value=types.SimpleNamespace(returncode=0)) as run:
            self.win._stop_helper_children()
        run.assert_called_once_with(
            ["taskkill", "/PID", "4321", "/T", "/F"],
            check=False,
            capture_output=True,
            creationflags=gui.subprocess.CREATE_NO_WINDOW,
        )
        self.assertEqual(calls, [("wait", 3)])
        self.assertIsNone(self.win._skill_learning_process)

    def test_invalid_saved_types_are_replaced_without_crashing(self):
        self.path.write_text(json.dumps({'farm/margin': 'bad', '_geometry': ['bad'] * 5,
                                        '_sched_start': 123, '_last_version': 'broken'}), encoding='utf-8')
        data = self.win._load_saved()
        self.assertNotIn('farm/margin', data)
        self.assertNotIn('_geometry', data)
        self.assertIn('잘못된 설정값', self.win._settings_load_warning)

    def test_invalid_config_is_reported_before_a_thread_starts(self):
        with patch.object(self.win, '_build_config', return_value={}), patch('gui.BotThread') as thread:
            self.win.start()
        thread.assert_not_called()
        self.assertIsNone(self.win.thread)
        self.assertTrue(self.win._last_error)

    def test_backup_restores_values_but_keeps_secret_fields(self):
        backup = Path(self.temp.name) / 'backup.json'
        backup.write_text(json.dumps({'farm/margin': 1.4, 'notify/telegram/token': 'injected'}), encoding='utf-8')
        self.win.controls[('notify', 'telegram', 'token')].setText('original')
        with patch('gui.QFileDialog.getOpenFileName', return_value=(str(backup), '')):
            self.win._restore_settings()
        self.assertEqual(self.win.controls[('farm', 'margin')].value(), 1.4)
        self.assertEqual(self.win.controls[('notify', 'telegram', 'token')].text(), 'original')
        self.assertTrue(self.win.btn_save_settings.isEnabled())


if __name__ == '__main__':
    unittest.main()
