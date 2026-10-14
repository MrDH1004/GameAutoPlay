r"""게임 입력 없이 제어판 각 화면을 렌더링한다.

    .venv\Scripts\python.exe -m tools.ui_preview
"""
import os
from pathlib import Path
import tempfile

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['DOKKAEBI_TEST'] = '1'

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
import gui


def main():
    app = QApplication.instance() or QApplication([])
    for font in ('malgun.ttf', 'malgunbd.ttf'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / font))
    app.setStyle('Fusion')
    app.setStyleSheet(gui.QSS)
    destination = Path(gui.ROOT) / 'screenshots' / 'ui-renewal'
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        original = gui.SETTINGS_PATH
        preview_settings = Path(temp) / 'settings.json'
        preview_settings.write_bytes(Path(original).read_bytes())
        gui.SETTINGS_PATH = str(preview_settings)
        win = gui.Window()
        win.timer.stop()
        if win.tray:
            win.tray.hide()
        win.resize(1000, 720)
        win.show()
        app.processEvents()
        win._geo_timer.stop()
        for index, name in enumerate(('dashboard', 'settings', 'stats', 'preview', 'map', 'recommend', 'log', 'support')):
            win.tabs.setCurrentIndex(index)
            app.processEvents()
            win.grab().save(str(destination / f'{name}.png'))
        win.settings_search.setText('물약')
        win.tabs.setCurrentIndex(1)
        app.processEvents()
        win.grab().save(str(destination / 'settings-search.png'))
        win.close()
        gui.SETTINGS_PATH = original
    print(destination)


if __name__ == '__main__':
    main()
