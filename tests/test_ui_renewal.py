"""UI 리뉴얼의 설정 저장·검색·실행 제어 회귀 검사. 게임 입력은 보내지 않는다."""
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('DOKKAEBI_TEST', '1')

from PySide6.QtWidgets import QApplication
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

    def test_stop_is_recorded_while_bot_is_initializing(self):
        self.win.thread = types.SimpleNamespace(bot=None, stop_requested=False)
        self.win.stop()
        self.assertTrue(self.win.thread.stop_requested)
        self.assertFalse(self.win.btn_stop.isEnabled())

    def test_save_failure_keeps_unsaved_changes(self):
        self.win.controls[('farm', 'margin')].setValue(1.5)
        with patch('builtins.open', side_effect=PermissionError('denied')):
            self.win._save_settings()
        self.assertIn('저장 실패', self.win.settings_status.text())
        self.assertTrue(self.win.btn_save_settings.isEnabled())


if __name__ == '__main__':
    unittest.main()
