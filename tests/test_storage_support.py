import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from execution_lock import acquire_bot_lock

from app_paths import initialize_data
from app_support import export_support
from storage import load_json, save_json


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'settings.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_corrupt_primary_recovers_previous_valid_settings(self):
        save_json(self.path, {'margin': 1.5})
        save_json(self.path, {'margin': 1.3})
        self.path.write_text('{broken', encoding='utf-8')
        data, notice = load_json(self.path)
        self.assertEqual(data, {'margin': 1.5})
        self.assertIn('복구', notice)
        save_json(self.path, data)
        self.assertEqual(load_json(self.path.with_suffix('.json.bak'))[0], {'margin': 1.5})

    def test_failed_replace_does_not_truncate_original_and_removes_temp(self):
        save_json(self.path, {'before': 1})
        with patch('storage.os.replace', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                save_json(self.path, {'after': 2})
        self.assertEqual(load_json(self.path)[0], {'before': 1})
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_non_object_settings_and_missing_file_are_distinguished(self):
        self.assertEqual(load_json(self.path), ({}, ''))
        self.path.write_text('[]', encoding='utf-8')
        self.assertIn('기본 설정', load_json(self.path)[1])

    def test_resource_update_keeps_configuration_database_and_learned_digits(self):
        source, target = self.root / 'bundle', self.root / 'user'
        (source / 'templates' / 'digits').mkdir(parents=True)
        (source / 'config.yaml').write_text('initial', encoding='utf-8')
        (source / 'farm_spots.json').write_text('{}', encoding='utf-8')
        (source / 'templates' / 'digits' / 'power_0.png').write_bytes(b'default')
        (source / 'templates' / 'field.png').write_bytes(b'old')
        initialize_data(source, target, '1.0.0')
        (target / 'config.yaml').write_text('custom', encoding='utf-8')
        (target / 'farm_spots.json').write_text('custom db', encoding='utf-8')
        (target / 'templates' / 'digits' / 'power_0.png').write_bytes(b'learned')
        (source / 'templates' / 'field.png').write_bytes(b'new')
        initialize_data(source, target, '1.1.0')
        self.assertEqual((target / 'config.yaml').read_text(encoding='utf-8'), 'custom')
        self.assertEqual((target / 'farm_spots.json').read_text(encoding='utf-8'), 'custom db')
        self.assertEqual((target / 'templates' / 'digits' / 'power_0.png').read_bytes(), b'learned')
        self.assertEqual((target / 'templates' / 'field.png').read_bytes(), b'new')

    def test_diagnostic_bundle_has_no_tokens_webhooks_or_screenshots(self):
        token = '123456:ABCDEFGHIJKLMNOPQRSTUV'
        webhook = 'https://discord.com/api/webhooks/123/secret-value'
        settings = {'notify/telegram/token': token, 'notify/telegram/chat_id': '998877',
                    'notify/discord/webhook': webhook, 'farm/margin': 1.5}
        path = self.root / 'support.zip'
        export_support(path, {'checks': []}, settings,
                       [f'failed https://api.telegram.org/bot{token}/getUpdates {webhook} chat=998877'])
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(set(archive.namelist()), {'environment.json', 'settings.json', 'recent.log', 'README.txt'})
            contents = '\n'.join(archive.read(name).decode('utf-8') for name in archive.namelist())
        for secret in (token, webhook, '998877'):
            self.assertNotIn(secret, contents)
        self.assertIn('1.5', contents)

    def test_second_bot_cannot_acquire_same_game_lock(self):
        config = {'process_name': self.temp.name}
        first = acquire_bot_lock(config)
        try:
            with self.assertRaises(RuntimeError):
                acquire_bot_lock(config)
        finally:
            first.unlock()
        second = acquire_bot_lock(config)
        second.unlock()


if __name__ == '__main__':
    unittest.main()
